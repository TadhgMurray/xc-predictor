import sys
sys.path.insert(0, "racecast")
from conversions import default_difficulty, venue_difficulty

print("XC default:", default_difficulty("XC"))
print("TF default:", default_difficulty("TF"))
print("Armory indoor:", venue_difficulty("TF", location_id=81310, is_indoor=1))