# override_distances.py
# Purpose: Overrides crazy distances for certain races that overinflate
# ratings for certain races. Does not apply to underinflation for now.

# season_rating_sql
# Gets the speed rating for that season of an athlete.
season_rating_sql = """

"""

# race_rating_sql
# Gets the speed rating for that race of an athlete.
race_rating_sql = """

"""

# These constants are the sane rating bands an athlete's race's should be in
# for each ability level. This was found through db querying and eyeing it.
# It is different for each pool. 
SANE_RATING_BAND = 


# loadRaceSeasonRatings
# Purpose: Loads the athletes' season ratings.
# Arguments:
def loadRaceSeasonRatings():

# loadRaceRatings
# Purpose: Loads the athletes' race ratings.
# Arguments:
def loadRaceSeasonRatings():

# checkIndividualSanity
# Purpose: Checks a race is within an athlete's sane rating band.
# Arguments:
def checkSanity():

# checkRaceSanity
# Purpose: Finds how many people within a race are within their sane rating
# band. DOES NOT COUNT PEOPLE WITH NO OTHER RACES.
# Arguments:
def checkRaceSanity():

# overrideDistance
# Purpose: If enough people are not within their sane rating band, proposes
# a new distance that centers them within their rating bands.
# Arguments:
def overrideDistance():

# writeDistanceOverride
# Purpose: Writes a distance override to a corrections file.
# Arguments:
def writeDistanceOverride():