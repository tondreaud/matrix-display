import time
import requests
from queue import LifoQueue

# BART line colors (official route colors)
LINE_COLORS = {
    'YELLOW': (255, 255, 51),
    'BLUE': (0, 99, 199),
    'RED': (237, 28, 36),
    'GREEN': (0, 133, 64),
    'ORANGE': (249, 145, 30),
    'PURPLE': (128, 0, 128),
    'BEIGE': (210, 180, 140),
    'WHITE': (200, 200, 200),
}

# Shorten long destination names for the 64px display
DESTINATION_ABBREVIATIONS = {
    'Antioch': 'Antioch',
    'Berryessa/North San Jose': 'Berryessa',
    'Dublin/Pleasanton': 'Dublin',
    'Daly City': 'Daly City',
    'Millbrae': 'Millbrae',
    'Pittsburg/Bay Point': 'Pittsburg',
    'Richmond': 'Richmond',
    'San Francisco International Airport': 'SFO',
    'SF Airport': 'SFO',
    'SFO/Millbrae': 'SFO',
    'Warm Springs/South Fremont': 'Warm Spgs',
    'Oakland International Airport': 'OAK',
    'OAK Airport': 'OAK',
}

BART_API_URL = 'https://api.bart.gov/api/etd.aspx'
BART_PUBLIC_KEY = 'MW9S-E7SL-26DU-VV8V'


class BARTModule:
    def __init__(self, config):
        self.invalid = False
        self.queue = LifoQueue()
        self.config = config
        self.last_fetch_time = 0
        self.fetch_interval = 30
        self.cached_arrivals = {'lane1': None, 'lane2': None}

        self.lanes = {}

        if config is not None and 'BARTLane1' in config:
            self.lanes['lane1'] = self._parse_lane_config(config, 'BARTLane1')
            self.lanes['lane2'] = self._parse_lane_config(config, 'BARTLane2')
            print(f"[BART Module] Initialized with 2 lanes:")
            print(f"  Lane 1: station={self.lanes['lane1']['station']}, dir={self.lanes['lane1']['direction']}, destinations={self.lanes['lane1']['destinations']}")
            print(f"  Lane 2: station={self.lanes['lane2']['station']}, dir={self.lanes['lane2']['direction']}, destinations={self.lanes['lane2']['destinations']}")
        else:
            print("[BART Module] Missing config parameters (need [BARTLane1] and [BARTLane2])")
            self.invalid = True

    def _parse_lane_config(self, config, section):
        station = config.get(section, 'station', fallback='embr')
        direction = config.get(section, 'direction', fallback='')
        destinations_str = config.get(section, 'destinations', fallback='')
        destinations = [d.strip() for d in destinations_str.split(',') if d.strip()] if destinations_str else []

        return {
            'station': station,
            'direction': direction,
            'destinations': destinations,
        }

    def _abbreviate_destination(self, dest):
        return DESTINATION_ABBREVIATIONS.get(dest, dest)

    def _fetch_lane_arrivals(self, lane_config):
        current_time = time.time()
        station = lane_config['station']
        direction_filter = lane_config['direction'].lower() if lane_config['direction'] else ''
        dest_filter = [d.lower() for d in lane_config['destinations']]

        try:
            params = {
                'cmd': 'etd',
                'orig': station,
                'key': BART_PUBLIC_KEY,
                'json': 'y',
            }
            if direction_filter in ('n', 's'):
                params['dir'] = direction_filter

            resp = requests.get(BART_API_URL, params=params, timeout=10)
            data = resp.json()

            station_data = data.get('root', {}).get('station', [])
            if not station_data:
                return None

            etds = station_data[0].get('etd', [])

            # Find the best matching destination for this lane
            best = None
            best_minutes = 999

            for etd in etds:
                dest_name = etd.get('destination', '')
                dest_abbr = etd.get('abbreviation', '')

                # Filter by destination if specified
                if dest_filter:
                    matches = any(
                        f in dest_name.lower() or f in dest_abbr.lower()
                        for f in dest_filter
                    )
                    if not matches:
                        continue

                estimates = etd.get('estimate', [])
                if not estimates:
                    continue

                times_for_dest = []
                line_color_name = estimates[0].get('color', 'WHITE')
                hex_color = estimates[0].get('hexcolor', '#ffffff')

                for est in estimates:
                    mins_str = est.get('minutes', '')
                    if mins_str == 'Leaving':
                        mins = 0
                    else:
                        try:
                            mins = int(mins_str)
                        except (ValueError, TypeError):
                            continue

                    times_for_dest.append({
                        'minutes': mins,
                        'arrival_timestamp': current_time + (mins * 60),
                        'terminal': dest_name,
                    })

                if times_for_dest:
                    times_for_dest.sort(key=lambda x: x['minutes'])
                    first_min = times_for_dest[0]['minutes']

                    if first_min < best_minutes:
                        best_minutes = first_min
                        # Parse hex color to RGB tuple
                        rgb = self._hex_to_rgb(hex_color)
                        best = {
                            'line': line_color_name.upper(),
                            'direction': self._abbreviate_destination(dest_name),
                            'times': times_for_dest[:3],
                            'color': rgb,
                        }

            return best

        except Exception as e:
            print(f"[BART Module] Error fetching arrivals for {station}: {e}")
            return None

    def _hex_to_rgb(self, hex_color):
        hex_color = hex_color.lstrip('#')
        if len(hex_color) == 6:
            return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))
        return (255, 255, 255)

    def get_line_color(self, line):
        return LINE_COLORS.get(line.upper(), (255, 255, 255))

    def getArrivals(self):
        if self.invalid:
            return []

        current_time = time.time()

        if current_time - self.last_fetch_time < self.fetch_interval:
            cached = self._get_cached_with_updated_times()
            if cached:
                return cached

        try:
            lane1_arrival = self._fetch_lane_arrivals(self.lanes['lane1'])
            lane2_arrival = self._fetch_lane_arrivals(self.lanes['lane2'])

            self.cached_arrivals = {
                'lane1': lane1_arrival,
                'lane2': lane2_arrival,
            }
            self.last_fetch_time = current_time

            result = []
            if lane1_arrival:
                result.append(lane1_arrival)
            if lane2_arrival:
                result.append(lane2_arrival)

            if result:
                self.queue.put(result)

            return result

        except Exception as e:
            print(f"[BART Module] Error fetching arrivals: {e}")
            return self._get_cached_with_updated_times() or []

    def _get_cached_with_updated_times(self):
        if not self.cached_arrivals['lane1'] and not self.cached_arrivals['lane2']:
            return None

        current_time = time.time()
        result = []

        for lane_key in ['lane1', 'lane2']:
            cached = self.cached_arrivals.get(lane_key)
            if cached:
                updated_times = []
                for t in cached['times']:
                    minutes = max(0, int((t['arrival_timestamp'] - current_time) / 60))
                    updated_times.append({
                        'minutes': minutes,
                        'arrival_timestamp': t['arrival_timestamp'],
                        'terminal': t.get('terminal'),
                    })

                if updated_times:
                    result.append({
                        'line': cached['line'],
                        'direction': cached['direction'],
                        'times': updated_times[:3],
                        'color': cached['color'],
                    })

        return result if result else None
