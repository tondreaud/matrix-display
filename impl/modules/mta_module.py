import time
from queue import LifoQueue

# MTA subway line colors (official colors)
LINE_COLORS = {
    # IRT Lines (numbered)
    '1': (238, 53, 46),    # Red
    '2': (238, 53, 46),    # Red
    '3': (238, 53, 46),    # Red
    '4': (0, 147, 60),     # Green
    '5': (0, 147, 60),     # Green
    '6': (0, 147, 60),     # Green
    '7': (185, 51, 173),   # Purple
    # IND Lines (letters)
    'A': (0, 57, 166),     # Blue
    'C': (0, 57, 166),     # Blue
    'E': (0, 57, 166),     # Blue
    'B': (255, 99, 25),    # Orange
    'D': (255, 99, 25),    # Orange
    'F': (255, 99, 25),    # Orange
    'M': (255, 99, 25),    # Orange
    'G': (108, 190, 69),   # Light Green
    'J': (153, 102, 51),   # Brown
    'Z': (153, 102, 51),   # Brown
    'L': (167, 169, 172),  # Gray
    'N': (252, 204, 10),   # Yellow
    'Q': (252, 204, 10),   # Yellow
    'R': (252, 204, 10),   # Yellow
    'W': (252, 204, 10),   # Yellow
    'S': (128, 129, 131),  # Shuttle Gray
    'SI': (0, 57, 166),    # Staten Island Railway - Blue
}

# Simplify terminal names to borough names (like real subway LED signs)
TERMINAL_SIMPLIFICATIONS = {
    # Brooklyn terminals
    'Canarsie-Rockaway Pkwy': 'Bklyn',
    # Manhattan terminals
    '8 Av': 'Mhtn',
}

class MTAModule:
    def __init__(self, config):
        self.invalid = False
        self.queue = LifoQueue()
        self.config = config
        self.last_fetch_time = 0
        self.fetch_interval = 30  # Fetch every 30 seconds
        self.cached_arrivals = {'lane1': None, 'lane2': None}
        
        # Parse lane configurations
        self.lanes = {}
        
        # Try new two-lane config format first
        if config is not None and 'SubwayLane1' in config:
            self.lanes['lane1'] = self._parse_lane_config(config, 'SubwayLane1')
            self.lanes['lane2'] = self._parse_lane_config(config, 'SubwayLane2')
            
            try:
                from nyct_gtfs import NYCTFeed  # type: ignore[import-not-found]
                self.NYCTFeed = NYCTFeed
                print(f"[MTA Module] Initialized with 2 lanes:")
                print(f"  Lane 1: stops={self.lanes['lane1']['stop_ids']}, dir={self.lanes['lane1']['direction']}, lines={self.lanes['lane1']['lines']}")
                print(f"  Lane 2: stops={self.lanes['lane2']['stop_ids']}, dir={self.lanes['lane2']['direction']}, lines={self.lanes['lane2']['lines']}")
            except ImportError as e:
                print(f"[MTA Module] nyct-gtfs not installed: {e}")
                self.invalid = True
                
        # Fallback to old single [Subway] config
        elif config is not None and 'Subway' in config:
            old_config = self._parse_lane_config(config, 'Subway')
            # Use same config for both lanes (old behavior)
            self.lanes['lane1'] = old_config
            self.lanes['lane2'] = old_config
            
            try:
                from nyct_gtfs import NYCTFeed  # type: ignore[import-not-found]
                self.NYCTFeed = NYCTFeed
                print(f"[MTA Module] Initialized (legacy mode) for stops {old_config['stop_ids']}, direction {old_config['direction']}, lines {old_config['lines']}")
            except ImportError as e:
                print(f"[MTA Module] nyct-gtfs not installed: {e}")
                self.invalid = True
        else:
            print("[MTA Module] Missing config parameters")
            self.invalid = True
    
    def _parse_lane_config(self, config, section):
        """Parse configuration for a single lane."""
        stop_ids_str = config.get(section, 'stop_ids', fallback='')
        if not stop_ids_str:
            stop_ids_str = config.get(section, 'stop_id', fallback='127')
        stop_ids = [s.strip() for s in stop_ids_str.split(',') if s.strip()]
        
        direction = config.get(section, 'direction', fallback='N')
        lines_str = config.get(section, 'lines', fallback='1,2,3')
        lines = [line.strip() for line in lines_str.split(',')]
        
        return {
            'stop_ids': stop_ids,
            'direction': direction,
            'lines': lines
        }
    
    def get_line_color(self, line):
        """Get the color for a subway line"""
        return LINE_COLORS.get(line.upper(), (255, 255, 255))
    
    def _simplify_terminal(self, terminal_name):
        """Simplify terminal station name to borough/neighborhood (like real subway signs)"""
        if not terminal_name:
            return terminal_name
        return TERMINAL_SIMPLIFICATIONS.get(terminal_name, terminal_name)
    
    def _get_direction_name(self, direction):
        """Get readable direction name based on direction code"""
        return "Uptown" if direction == 'N' else "Downtown"
    
    def _fetch_lane_arrivals(self, lane_config):
        """Fetch arrivals for a single lane configuration."""
        current_time = time.time()
        line_arrivals = {}
        
        for line in lane_config['lines']:
            try:
                feed = self.NYCTFeed(line)
                times_for_line = []
                
                # Try each stop ID for this line
                for stop_id in lane_config['stop_ids']:
                    stop_id_with_dir = f"{stop_id}{lane_config['direction']}"
                    
                    # Filter trips headed to our stop
                    trips = feed.filter_trips(
                        line_id=[line.upper()],
                        headed_for_stop_id=[stop_id_with_dir],
                        underway=True
                    )
                    
                    for trip in trips:
                        # Get the terminal station (last stop on the trip)
                        terminal_station = None
                        if trip.stop_time_updates:
                            terminal_station = trip.stop_time_updates[-1].stop_name
                        
                        # Find the stop time for our stop
                        for stop_update in trip.stop_time_updates:
                            if stop_update.stop_id == stop_id_with_dir:
                                arrival_time = stop_update.arrival
                                if arrival_time:
                                    minutes_away = max(0, int((arrival_time.timestamp() - current_time) / 60))
                                    times_for_line.append({
                                        'minutes': minutes_away,
                                        'arrival_timestamp': arrival_time.timestamp(),
                                        'terminal': terminal_station
                                    })
                                break
                
                # Sort times and keep top 3
                times_for_line.sort(key=lambda x: x['minutes'])
                if times_for_line:
                    # Use the terminal station from the first train as the direction
                    # Simplify to borough/neighborhood name like real subway signs
                    raw_terminal = times_for_line[0].get('terminal')
                    terminal = self._simplify_terminal(raw_terminal) or self._get_direction_name(lane_config['direction'])
                    line_arrivals[line.upper()] = {
                        'line': line.upper(),
                        'direction': terminal,
                        'times': times_for_line[:3],
                        'color': self.get_line_color(line)
                    }
                            
            except Exception as e:
                print(f"[MTA Module] Error fetching line {line}: {e}")
                continue
        
        # Convert to list sorted by first arrival time
        result = list(line_arrivals.values())
        result.sort(key=lambda x: x['times'][0]['minutes'] if x['times'] else 999)
        
        # Return the first (soonest) train for this lane
        return result[0] if result else None
    
    def getArrivals(self):
        """Fetch upcoming train arrivals for both lanes."""
        if self.invalid:
            return []
        
        current_time = time.time()
        
        # Use cached data if within fetch interval
        if current_time - self.last_fetch_time < self.fetch_interval:
            cached = self._get_cached_with_updated_times()
            if cached:
                return cached
        
        try:
            # Fetch arrivals for each lane
            lane1_arrival = self._fetch_lane_arrivals(self.lanes['lane1'])
            lane2_arrival = self._fetch_lane_arrivals(self.lanes['lane2'])
            
            # Cache the results
            self.cached_arrivals = {
                'lane1': lane1_arrival,
                'lane2': lane2_arrival
            }
            self.last_fetch_time = current_time
            
            # Build result list
            result = []
            if lane1_arrival:
                result.append(lane1_arrival)
            if lane2_arrival:
                result.append(lane2_arrival)
            
            # Put in queue for display
            if result:
                self.queue.put(result)
            
            return result
            
        except Exception as e:
            print(f"[MTA Module] Error fetching arrivals: {e}")
            return self._get_cached_with_updated_times() or []
    
    def _get_cached_with_updated_times(self):
        """Get cached arrivals with updated time calculations."""
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
                    if minutes >= 0:
                        updated_times.append({
                            'minutes': minutes,
                            'arrival_timestamp': t['arrival_timestamp'],
                            'terminal': t.get('terminal')
                        })
                
                if updated_times:
                    result.append({
                        'line': cached['line'],
                        'direction': cached['direction'],
                        'times': updated_times[:3],
                        'color': cached['color']
                    })
        
        return result if result else None
