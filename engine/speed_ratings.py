# Project: xc-predictor
# Author: Tadhg Murray
# Subset: Speed Rating Engine
# Date: 6/3/2026
# File Title: speed_ratings.py
# Purpose: Database read and write functions for the speed rating engine.
#          Separate from scripts/database.py which handles scraper writes.

import sys
import math
from datetime import date, datetime
from collections import defaultdict
from normalize_distance import getPool
from speed_ratings_db import loadResults, saveCourseDifficulties, saveAthleteRatings, saveResultSpeedRatings

# Add scripts folder to path so we can import our other scripts files.
sys.path.insert(0, "scripts")
from database import DB_PATH, migrateAddSpeedRating, createTables

# ------------------------------------------------------------------ #
# CONSTANTS
# ------------------------------------------------------------------ #

# Decay factor per day. A race from 365 days ago gets weight
# 0.998^365 = 0.48 — about half as relevant as today.
# A race from 2 years ago gets 0.998^730 = 0.23.
DECAY_K = 0.998

# Stop iterating when average absolute change in course difficulties
# drops below this threshold. 0.001 = 0.1%.
CONVERGENCE_THRESHOLD = 0.001

# Maximum iterations regardless of convergence — prevents infinite
# loops on courses with very few athletes where difficulties oscillate.
MAX_ITERATIONS = 50

# Drop performances more than this many standard deviations from an
# athlete's weighted mean. 2.0 is the standard statistical threshold
# for outliers — covers ~95% of a normal distribution.
OUTLIER_STD_THRESHOLD = 2.0

# Minimum races needed before we compute an athlete's speed rating.
# Athletes with fewer races than this get skipped — not enough data
# to compute a reliable ability estimate.
MIN_RACES = 3

# Blends old and new course difficulties each iteration to prevent
# oscillation around the true answer. 0.3 means take 30% of the new
# estimate and keep 70% of the old each step — converges slower but
# stably without bouncing around the true value.
# Same concept as learning rate in gradient descent.
DAMPING = 0.3

# ------------------------------------------------------------------ #
# DECAY WEIGHTING
# ------------------------------------------------------------------ #

# computeDecayWeight
# Purpose: Computes the recency weight for a single performance based
#          on how many days ago it happened. Use an exponential decay.
# Arguments:
#           race_date_str: date string of the race from the db, format "YYYY-MM-DD".
#           today: today's date as a datetime.date object.
# Output: Returns a float weight between 0 and 1. Returns 0.0 if the date or
#         date string is invalid.
def computeDecayWeight(race_date_str: str, today: date) -> float:

    try:
        # Parse the date string into a datetime.date object so we can
        # subtract dates. strptime means "string parse time".
        # "%Y-%m-%d" is the format - %Y is 4-digit year, %m is month,
        # %d is day. .date() extracts just the date part
        race_date = datetime.strptime(race_date_str, "%Y-%m-%d").date()
        
    except (ValueError, TypeError):
        # Invalid date string - return 0 so this result gets effectively
        # ignores in weighted averages.
        return 0.0
    
    # (today - race_date) is a timedelta object - the difference between
    # two dayes. .days extracts the number of days as an integer.
    days_ago = (today - race_date).days

    # Negative days means a future date - shouldn't happen so
    #  return 0 so this result gets effectively ignores in weighted averages.
    if days_ago < 0:
        return 0.0
    
    # Exponential decay: weight = k ^ how many days ago.
    return math.pow(DECAY_K, days_ago)


# computeAthleteAbility
# Purpose: Computes a single athlete's true ability as a recency-weighted
#          average of their course-adjuisted performances. Can also
#          drop outliers before computing the final average.
# Arguments:
#           performances: list of dicts, each with keys:
#                         "adjusted_time" (normalized_time / (1 + difficulty)),
#                         "weight" (decay weight for this race).
#           remove_outliers: if True drop performances more than 2 std from
#                            the mean.
# Output: Returns ability as a float (flat 5k equivalenty seconds), or None if
#         not enough data.
def computeAthleteAbility(performance: list, remove_outliers: bool) -> float | None:

    # Need at leat MIN_RACES performances to compute a reliable ability.
    if len(performance) < MIN_RACES:
        return None
    
    # Step 1: compute weighted mean.
    # Weighted mean = sum(value * weight) / sum(weights).
    # This gives more influence to recent races than old ones.
    total_weight = sum(p["weight"] for p in performance)

    # If all weights are 0 (e.g. all races are ancient or invalid dates),
    # return None.
    if total_weight == 0:
        return None
    
    weighted_mean = sum(p["adjusted_time"] * p["weight"] for p in performance) / total_weight

    # Step 2 - optionally remove outliers using previously calculated weighted mean.
    if remove_outliers:

        # Compute weighted standard deviations by calculating the Z-score.
        # Variance = sum(weight * (value - mean) ^ 2) / sum(weights).
        # Standard deviation = sqrt(variance)
        variance = sum(
            p["weight"] * (p["adjusted_time"] - weighted_mean) ** 2
            for p in performance
        ) / total_weight

        std = math.sqrt(variance)

        # Keep only performance within OUTLIER_STD_THRESHOLD std devs
        # of the weighted mean.
        filtered = []
        for p in performance:
            if std == 0 or abs(p["adjusted_time"] - weighted_mean) <= OUTLIER_STD_THRESHOLD * std:
                filtered.append(p)
        
        # If filtering removed too many races, fall back to unfiltered.
        # This handles the edge case where an athlete has only 3 races
        # and one is an outlier — we don't want to drop below MIN_RACES.
        if len(filtered) < MIN_RACES:
            filtered = performance

        # Recompute weighted mean on filtered perfomrances.
        total_weight = sum(p["weight"] for p in filtered)
        
        if total_weight == 0:
            return None
        
        weighted_mean = sum(p["adjusted_time"] * p["weight"] for p in filtered) / total_weight

    return weighted_mean

# ------------------------------------------------------------------ #
# MAIN ENGINE
# ------------------------------------------------------------------ #

# runEngine
# Purpose: Loads results, runs the iterative algorithm, saves course
#          difficulties and athlete speed ratings to the DB. Not resume-safe.
#          Also saves each result speed rating to the DB.
# Arguments: None.
# Output: None. Writes results to DB.
def runEngine():

    createTables()

    # Run migration to add speed_rating column if it doesn't exist yet.
    # Safe to call every run — skips silently if column already exists.
    migrateAddSpeedRating()

    # Today's date - used for all decay weight calculations.
    today = date.today()

    # ---- Load data ------------------------------------------------ #

    print("Loading results from database...")

    raw_results = loadResults()

    if not raw_results:
        print("No results found. Exiting.")
        return
    
    # ---- Classify pools and attach decay weights ------------------- #

    # results is a list of dicst, one per performance. We add two fields:
    # "pool" (competitive pool like "hs_m") and "weight" (decay weight).
    # We also drop results where the pool is unknown_level since we
    # can't compare them faily to others.
    # TO-DO: unknown_level.
    results = []
    for r in raw_results:
        pool = getPool(r["grade"], r["gender"])
        if pool == "unknown_level":
            continue
        weight = computeDecayWeight(r["date"], today)
        if weight == 0:
            continue
        r["pool"] = pool
        r["weight"] = weight
        results.append(r)

    print(f"Using {len(results):,} results after pool classification and decay filtering")

    # ---- Group results by course and by athlete ------------------- #
    
    # defaultdict(list) creates a dictionary where accessing a missing key
    # automatically creates an empty list rather than raising KeyError.
    # This means we can do results_by_course["new course"].append(r)
    # without first checking if "new course" exists.

    # results_by_course: all the results (values) for a course (key: course_name).
    results_by_course = defaultdict(list)

    # results_by_athlete: all the results (values) for an athlete in a certain pool
    # (key: {athlete_id, pool}) There can be multiple of the same athlete in 
    # different pools.
    results_by_athlete = defaultdict(list)

    # For each result add it to it's respective athlete and course list dict.
    for r in results:
        results_by_course[r["course_name"]].append(r)
        results_by_athlete[(r["athlete_id"], r["pool"])].append(r)

    print(f"Found {len(results_by_course):,} courses and {len(results_by_athlete):,} athletes")

    # ---- Initialize course difficulties --------------------------- #

    # All courses start at 0.0 — flat neutral. This is a dictionary:
    # {course_name: difficulty_as_decimal} e.g. {"Detweiller": 0.03}
    course_difficulties = {course: 0.0 for course in results_by_course}

    # ---- Iterative convergence loop ------------------------------- #

    print("\nStarting iterative convergence...")

    # float('inf') means infinity — any real number will be lower on
    # the first iteration, so best_change gets set immediately.
    best_change = float('inf')

    # Saves a copy of the difficulties from the best iteration.
    # .copy() makes a new dict — without it this would just be a
    # reference to course_difficulties and get overwritten every iteration.
    best_difficulties = course_difficulties.copy()

    # Counts how many iterations in a row we've gone without improving.
    iterations_without_improvement = 0

    # How many consecutive non-improving iterations before we stop.
    PATIENCE = 5
    
    for iteration in range(MAX_ITERATIONS):

        # On iteration 0 we don't remove outlier because athlete abilities
        # are based on raw time w/o course adjustment yet, so outliers
        # might just be hard courses.
        if iteration > 0:
            remove_outliers = True
        else: 
            remove_outliers = False

        # Step 1 — compute athlete abilities using current course difficulties.

        # This is a dict: {(athlete_id, pool): ability_in_seconds}
        athlete_abilities = {}

        # for (key), value in this dict create the performances list with
        # the race weight and adjusted time based on course difficulty.
        for (athlete_id, pool), athlete_results in results_by_athlete.items():

            # Build performances list - each entry has adjusted-time and weight.
            performances = [
                {
                    # Divide out course difficulty to get flat equivalent.
                    # (1 + difficulty) converts percentage to multiplier:
                    # difficulty = 0.03 means teh course is 3% harder,
                    # so dividing it by 1.03 gives the flat equivalent time.
                    "adjusted_time": r["normalized_time"] / (1 + course_difficulties.get(r["course_name"], 0.0)),
                    "weight": r["weight"]
                }
                for r in athlete_results
            ]

            ability = computeAthleteAbility(performances, remove_outliers)
            if ability is not None:
                athlete_abilities[(athlete_id, pool)] = ability

        # Step 2: recompute course difficulties using new athlete abilities
        new_difficulties = {}

        # For each course, look at every result run there, compare it to 
        # what the athlete was expected to run based on their ability,
        # and average those deviations together to get the course difficulty.
        for course_name, course_results in results_by_course.items():

            # Collect percentage deviations for results where we know
            # the athlete's ability.
            deviations = []
            athlete_ids_seen = set()

            # For each result for the course calculate how many stds from
            # the athlete's expected time it is. Add all the stds together
            # and divide by how many there are to get the new course difficulty.
            for r in course_results:
                key = (r["athlete_id"], r["pool"])
                ability = athlete_abilities.get(key)

                # Skip results where we couldn't compute athlete ability
                if ability is None or ability == 0:
                    continue

                deviation = (r["normalized_time"] / ability) - 1.0
                deviations.append(deviation)
                athlete_ids_seen.add(r["athlete_id"])

            if not deviations:
                # No usables results for this course - keep difficulty at 0.
                new_difficulties[course_name] = 0.0
            else:
                # Course difficulty = average deviation across all results.
                new_difficulties[course_name] = sum(deviations) / len(deviations)

        # Step 3: check convergence.
        # For every course, compute how much its difficulty changed this iteration.
        # If the average change across all courses is below the threshold, we're done.
        total_change = 0.0

        for c in course_difficulties:
            # .get(c, 0.0) returns 0.0 if the course isn't in the dict yet,
            # which handles the first iteration safely.
            total_change += abs(new_difficulties.get(c, 0.0) - course_difficulties.get(c, 0.0))

        if len(course_difficulties) == 0:
            avg_change = 0.0
        else:
            avg_change = total_change / len(course_difficulties)
            
        # :.6f in the print statement means 6 decimal places
        print(f"Iteration {iteration + 1}: avg change = {avg_change:.6f}")

        # Update course difficulties for next iteration.
        # Blend old and new difficulties using damping factor.
        # Without damping the algorithm overshoots slightly each iteration
        # and oscillates around the true answer instead of settling on it.
        course_difficulties = {
            course: (1 - DAMPING) * course_difficulties.get(course, 0.0) + DAMPING * new_difficulties.get(course, 0.0)
            for course in new_difficulties
        }

        # If this iteration improved on the best seen so far, save it
        # and reset the patience counter.
        # If not, increment — we're one step closer to giving up.
        if avg_change < best_change:
            best_change = avg_change
            best_difficulties = course_difficulties.copy()
            iterations_without_improvement = 0
        else:
            iterations_without_improvement += 1
            print(f"  No improvement ({iterations_without_improvement}/{PATIENCE})")

        # Stop condition 1: hit the convergence threshold.
        if avg_change < CONVERGENCE_THRESHOLD:
            print(f"Converged after {iteration + 1} iterations")
            break

        # Stop condition 2: plateaued — no improvement in PATIENCE iterations.
        # Restore the best state we saw before things got worse.
        if iterations_without_improvement >= PATIENCE:
            print(f"No improvement for {PATIENCE} iterations, stopping early")
            course_difficulties = best_difficulties
            break

    # The else on a for loop runs only if the loop finished without hitting
    # a break — meaning we hit MAX_ITERATIONS without converging or plateauing.
    # Restore best here too so we always end on the best state.
    else:
        print(f"Hit maximum {MAX_ITERATIONS} iterations without convergence")
        course_difficulties = best_difficulties
    
    # ---- Compute final course difficulty metadata ----------------- #

    print("\nComputing final course metadata...")

    difficulties_to_save = {}

    # Build the difficulties dict for saving to DB.
    # Includes n_results and n_athletes for confidence flaggi
    for course_name, difficulty, in course_difficulties.items():
        # Gets all the results for a course.
        course_results = results_by_course[course_name]
        # Gets number of athletes by putting all athlete ids from results
        # into a set, getting rid of duplicates.
        n_athletes = len(set(r["athlete_id"] for r in course_results))
        difficulties_to_save[course_name] = {
            "difficulty": difficulty,
            "n_results": len(course_results),
            "n_athletes": n_athletes
        }

    # ---- Convert athlete abilities to points scale ---------------- #

    print("Computing points scale per pool...")

    # Group abilities by pool so we can compute per-pool averages.
    # {pool: [ablity, ability, ...]}
    abilities_by_pool = defaultdict(list)
    # For each (key), value pair.
    for (athlete_id, pool), ability in athlete_abilities.items():
        abilities_by_pool[pool].append(ability)
    
    # Compute mean ability per pool - this becomes 100-point anchor.
    # {pool: mean_ability_in_seconds}. if ability means do this if ability exists.
    pool_means = {
        pool: sum(abilities) / len(abilities)
        for pool, abilities in abilities_by_pool.items()
        if abilities
    }

    # Convert each athlete ability to points.
    # Formula: points = (pool_mean / ability) * 100
    # Dividing pool_mean by ability means:
    # - faster than average (lower seconds) -> above 100
    # - slower than average (higher seconds) -> below 100
    ratings_to_save = {}
    for (athlete_id, pool), ability in athlete_abilities.items():
        pool_mean = pool_means.get(pool)
        if pool_mean is None or pool_mean == 0:
            continue

        points = (pool_mean / ability) * 100

        # Count races for this athlete in this pool
        n_races = len(results_by_athlete[(athlete_id, pool)])

        ratings_to_save[(athlete_id, pool)] = {
            "speed_rating": round(points, 2),
            "n_races": n_races
        }

    # ---- Compute per-result speed ratings ------------------------- #

    print("Computing per-result speed ratings...")

    # result_ratings maps result_id -> speed_rating for every result
    # where we have both a course difficulty and a pool mean.
    result_ratings = {}

    # For each result calculates a speed rating for it based on
    # course difficulty, the distance normalized time, and the
    # pool mean.
    for r in results:

        pool_mean = pool_means.get(r["pool"])
        difficulty = course_difficulties.get(r["course_name"], 0.0)

        # Skip if we don't have a pool mean for this pool.
        if pool_mean is None or pool_mean == 0:
            continue

        # Adjust the normalized time for course difficulty to get the
        # flat equivalent — same formula as athlete ability computation
        # but applied to a single result with no weighting or outlier removal.
        # This is what the athlete actually ran, expressed as a flat 5K time.
        adjusted_time = r["normalized_time"] / (1 + difficulty)

        # Convert to points — same formula as athlete ratings.
        # pool_mean / adjusted_time means faster times get higher points.
        speed_rating = (pool_mean / adjusted_time) * 100

        result_ratings[r["result_id"]] = round(speed_rating, 2)

    print(f"Computed {len(result_ratings):,} per-result speed ratings")

    # ---- Save to database ----------------------------------------- #

    print("\nSaving to database...")
    saveCourseDifficulties(difficulties_to_save)
    saveAthleteRatings(ratings_to_save)
    saveResultSpeedRatings(result_ratings)

    print("\nEngine complete.")
    print(f"Courses rated: {len(difficulties_to_save):,}")
    print(f"Athletes rated: {len(ratings_to_save):,}")


if __name__ == "__main__":
    runEngine()


