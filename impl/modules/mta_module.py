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

class MTAModule:
    def __init__(self, config):
        self.invalid = False
        self.queue = LifoQueue()
        self.config = config
        self.last_fetch_time = 0
        self.fetch_interval = 30  # Fetch every 30 seconds
        self.cached_arrivals = {}
        
        # Parse config
        if config is not None and 'Subway' in config:
            # Support multiple stop IDs (comma-separated)
            stop_ids_str = config.get('Subway', 'stop_ids', fallback='')
            if not stop_ids_str:
                # Fallback to legacy single stop_id
                stop_ids_str = config.get('Subway', 'stop_id', fallback='127')
            self.stop_ids = [s.strip() for s in stop_ids_str.split(',') if s.strip()]
            
            self.direction = config.get('Subway', 'direction', fallback='N')
            lines_str = config.get('Subway', 'lines', fallback='1,2,3')
            self.lines = [line.strip() for line in lines_str.split(',')]
            
            try:
                from nyct_gtfs import NYCTFeed  # type: ignore[import-not-found]
                self.NYCTFeed = NYCTFeed
                print(f"[MTA Module] Initialized for stops {self.stop_ids}, direction {self.direction}, lines {self.lines}")
            except ImportError as e:
                print(f"[MTA Module] nyct-gtfs not installed: {e}")
                self.invalid = True
        else:
            print("[MTA Module] Missing config parameters")
            self.invalid = True
    
    def get_line_color(self, line):
        """Get the color for a subway line"""
        return LINE_COLORS.get(line.upper(), (255, 255, 255))
    
    def _get_direction_name(self):
        """Get readable direction name based on direction code"""
        return "Uptown" if self.direction == 'N' else "Downtown"
    
    def getArrivals(self):
        """Fetch upcoming train arrivals grouped by line"""
        if self.invalid:
            return []
        
        current_time = time.time()
        
        # Use cached data if within fetch interval
        if current_time - self.last_fetch_time < self.fetch_interval and self.cached_arrivals:
            return self._update_grouped_times(self.cached_arrivals)
        
        try:
            # Group arrivals by line
            line_arrivals = {}
            
            for line in self.lines:
                try:
                    feed = self.NYCTFeed(line)
                    times_for_line = []
                    
                    # Try each stop ID for this line
                    for stop_id in self.stop_ids:
                        stop_id_with_dir = f"{stop_id}{self.direction}"
                        
                        # Filter trips headed to our stop
                        trips = feed.filter_trips(
                            line_id=[line.upper()],
                            headed_for_stop_id=[stop_id_with_dir],
                            underway=True
                        )
                        
                        for trip in trips:
                            # Find the stop time for our stop
                            for stop_update in trip.stop_time_updates:
                                if stop_update.stop_id == stop_id_with_dir:
                                    arrival_time = stop_update.arrival
                                    if arrival_time:
                                        minutes_away = max(0, int((arrival_time.timestamp() - current_time) / 60))
                                        times_for_line.append({
                                            'minutes': minutes_away,
                                            'arrival_timestamp': arrival_time.timestamp()
                                        })
                                    break
                    
                    # Sort times and keep top 3
                    times_for_line.sort(key=lambda x: x['minutes'])
                    if times_for_line:
                        line_arrivals[line.upper()] = {
                            'line': line.upper(),
                            'direction': self._get_direction_name(),
                            'times': times_for_line[:3],
                            'color': self.get_line_color(line)
                        }
                                
                except Exception as e:
                    print(f"[MTA Module] Error fetching line {line}: {e}")
                    continue
            
            self.cached_arrivals = line_arrivals
            self.last_fetch_time = current_time
            
            # Convert to list sorted by first arrival time
            result = list(line_arrivals.values())
            result.sort(key=lambda x: x['times'][0]['minutes'] if x['times'] else 999)
            
            # Put top 2 lines in queue for display
            top_lines = result[:2]
            if top_lines:
                self.queue.put(top_lines)
            
            return top_lines
            
        except Exception as e:
            print(f"[MTA Module] Error fetching arrivals: {e}")
            return self._update_grouped_times(self.cached_arrivals) if self.cached_arrivals else []
    
    def _update_grouped_times(self, cached):
        """Update cached arrival times based on current time"""
        current_time = time.time()
        result = []
        
        for line_data in cached.values():
            updated_times = []
            for t in line_data['times']:
                minutes = max(0, int((t['arrival_timestamp'] - current_time) / 60))
                if minutes >= 0:
                    updated_times.append({
                        'minutes': minutes,
                        'arrival_timestamp': t['arrival_timestamp']
                    })
            
            if updated_times:
                result.append({
                    'line': line_data['line'],
                    'direction': line_data['direction'],
                    'times': updated_times[:3],
                    'color': line_data['color']
                })
        
        result.sort(key=lambda x: x['times'][0]['minutes'] if x['times'] else 999)
        return result[:2]
