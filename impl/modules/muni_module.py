import time
import requests
from datetime import datetime
from queue import LifoQueue

# SF Muni Metro line colors (official)
MUNI_LINE_COLORS = {
    'J':  (244, 164, 24),   # J Church - orange
    'K':  (0, 135, 82),     # K Ingleside - green
    'T':  (0, 135, 82),     # T Third - green
    'KT': (0, 135, 82),     # KT combined - green
    'L':  (255, 205, 0),    # L Taraval - gold
    'M':  (238, 50, 36),    # M Ocean View - red
    'N':  (0, 45, 98),      # N Judah - dark blue
    'S':  (128, 128, 128),  # S Castro Shuttle - gray
}

# Default color for bus lines without a defined color (SF Muni brand red)
DEFAULT_MUNI_COLOR = (204, 0, 0)

# Shorten common SF Muni destination names for the small display
DESTINATION_SIMPLIFICATIONS = {
    'EMBARCADERO STATION':     'Embarc',
    'CASTRO STATION':          'Castro',
    'DALY CITY BART STATION':  'Daly Cy',
    'BALBOA PARK STATION':     'Balboa',
    'CALTRAIN':                'Caltrn',
    'OCEAN BEACH':             'Ocean',
    'JUDAH & 9TH AVE':         'Judah',
    'WEST PORTAL STATION':     'W Portal',
    'SF STATE':                'SF St',
    'SUNNYDALE':               'Snnydale',
    'VISITACION VALLEY':       'Vis Vly',
    'MISSION & GENEVA':        'Mission',
    'BAYSHORE':                'Bayshr',
    'FISHERMANS WHARF':        "Fish Wrf",
    "FISHERMAN'S WHARF":       'Fish Wrf',
    'TRANSBAY TERMINAL':       'Transbay',
    'SALESFORCE TRANSIT CTR':  'SFTransit',
}


def _simplify_destination(name):
    if not name:
        return name
    upper = name.strip().upper()
    for key, short in DESTINATION_SIMPLIFICATIONS.items():
        if key in upper:
            return short
    # Truncate to 9 chars so it fits on the display
    return name.strip()[:9]


class MuniModule:
    def __init__(self, config):
        self.invalid = False
        self.queue = LifoQueue()
        self.config = config
        self.last_fetch_time = 0
        self.fetch_interval = 30
        self.cached_arrivals = {'lane1': None, 'lane2': None}
        self.lanes = {}

        if config is None:
            print("[Muni Module] No config provided")
            self.invalid = True
            return

        self.api_key = config.get('511', 'api_key', fallback='').strip()
        if not self.api_key:
            print("[Muni Module] Missing 511 API key — add api_key under [511] in config.ini")
            self.invalid = True
            return

        if 'MuniLane1' in config:
            self.lanes['lane1'] = self._parse_lane_config(config, 'MuniLane1')
            self.lanes['lane2'] = self._parse_lane_config(config, 'MuniLane2')
            print("[Muni Module] Initialized with 2 lanes:")
            print(f"  Lane 1: stops={self.lanes['lane1']['stop_ids']}, dir={self.lanes['lane1']['direction']}, lines={self.lanes['lane1']['lines']}")
            print(f"  Lane 2: stops={self.lanes['lane2']['stop_ids']}, dir={self.lanes['lane2']['direction']}, lines={self.lanes['lane2']['lines']}")
        else:
            print("[Muni Module] Missing MuniLane1/MuniLane2 config sections")
            self.invalid = True

    def _parse_lane_config(self, config, section):
        stop_ids_str = config.get(section, 'stop_ids', fallback='')
        stop_ids = [s.strip() for s in stop_ids_str.split(',') if s.strip()]
        direction = config.get(section, 'direction', fallback='IB').strip().upper()
        lines_str = config.get(section, 'lines', fallback='')
        lines = [l.strip().upper() for l in lines_str.split(',') if l.strip()]
        return {'stop_ids': stop_ids, 'direction': direction, 'lines': lines}

    def get_line_color(self, line):
        return MUNI_LINE_COLORS.get(line.upper(), DEFAULT_MUNI_COLOR)

    def _fetch_stop_visits(self, stop_id):
        """Call the 511 StopMonitoring API for a single stop. Returns list of MonitoredStopVisit."""
        url = (
            f"http://api.511.org/transit/StopMonitoring"
            f"?api_key={self.api_key}&agency=SF&stopCode={stop_id}&format=json"
        )
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        # 511 sometimes returns BOM-prefixed UTF-8; decode manually to be safe
        text = resp.content.decode('utf-8-sig')
        import json
        data = json.loads(text)
        delivery = data.get('ServiceDelivery', {}).get('StopMonitoringDelivery', {})
        # StopMonitoringDelivery can be a list or dict depending on API version
        if isinstance(delivery, list):
            delivery = delivery[0] if delivery else {}
        return delivery.get('MonitoredStopVisit', [])

    def _fetch_lane_arrivals(self, lane_config):
        """Fetch and return the soonest arrival dict for one lane, or None."""
        current_time = time.time()
        line_arrivals = {}

        for stop_id in lane_config['stop_ids']:
            try:
                visits = self._fetch_stop_visits(stop_id)
            except Exception as e:
                print(f"[Muni Module] Error fetching stop {stop_id}: {e}")
                continue

            for visit in visits:
                journey = visit.get('MonitoredVehicleJourney', {})
                line_ref = journey.get('LineRef', '').strip().upper()
                direction_ref = journey.get('DirectionRef', '').strip().upper()

                # Filter by configured lines if specified
                if lane_config['lines'] and line_ref not in lane_config['lines']:
                    continue

                # Filter by direction (IB = inbound, OB = outbound)
                if lane_config['direction'] in ('IB', 'OB') and direction_ref != lane_config['direction']:
                    continue

                monitored_call = journey.get('MonitoredCall', {})
                expected_arrival = (
                    monitored_call.get('ExpectedArrivalTime') or
                    monitored_call.get('AimedArrivalTime')
                )
                if not expected_arrival:
                    continue

                try:
                    arrival_dt = datetime.fromisoformat(expected_arrival)
                    arrival_ts = arrival_dt.timestamp()
                    minutes_away = max(0, int((arrival_ts - current_time) / 60))
                except Exception:
                    continue

                raw_dest = (
                    monitored_call.get('DestinationDisplay') or
                    journey.get('DestinationName') or
                    direction_ref
                )
                destination = _simplify_destination(raw_dest)

                if line_ref not in line_arrivals:
                    line_arrivals[line_ref] = {
                        'line': line_ref,
                        'direction': destination,
                        'times': [],
                        'color': self.get_line_color(line_ref),
                    }

                line_arrivals[line_ref]['times'].append({
                    'minutes': minutes_away,
                    'arrival_timestamp': arrival_ts,
                    'terminal': destination,
                })

        # Sort each line's times, keep top 3
        for entry in line_arrivals.values():
            entry['times'].sort(key=lambda x: x['minutes'])
            entry['times'] = entry['times'][:3]
            # Use the soonest train's destination as direction label
            if entry['times']:
                entry['direction'] = entry['times'][0]['terminal'] or entry['direction']

        # Return the line with the soonest first arrival
        result = [e for e in line_arrivals.values() if e['times']]
        result.sort(key=lambda x: x['times'][0]['minutes'])
        return result[0] if result else None

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

            self.cached_arrivals = {'lane1': lane1_arrival, 'lane2': lane2_arrival}
            self.last_fetch_time = current_time

            result = [a for a in [lane1_arrival, lane2_arrival] if a is not None]
            if result:
                self.queue.put(result)
            return result

        except Exception as e:
            print(f"[Muni Module] Unexpected error: {e}")
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
