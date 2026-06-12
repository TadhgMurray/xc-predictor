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

# How many consecutive non-improving iterations before we stop.
PATIENCE = 5

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

# ------------------------------------------------------------------ #
# POOL CLASSIFICATION AND WEIGHT PRE-COMPUTATION
# ------------------------------------------------------------------ #

# _classifyAndWeight
# Purpose: Assigns pool and decay weight to every result once, before
#          the iteration loop starts. This is the key optimization —
#          weights never change between iterations (they're based on
#          race date which is fixed), so computing them once instead
#          of once per iteration per athlete saves millions of redundant
#          math.pow() calls.
#          e.g. 4M athletes * 15 iterations = 60M redundant weight
#          computations eliminated.
# Arguments:
#           raw_results: list of dicts from loadResults().
#           today: today's date for decay weight calculation.
# Output: Filtered list of result dicts with "pool" and "weight" added.
def _classifyAndWeight(raw_results: list, today: date) -> list:

    results = []
 
    for r in raw_results:
 
        # Classify into pool — drops unknown_level results.
        pool = getPool(r["grade"], r["gender"])
        if pool == "unknown_level":
            continue
 
        # Compute decay weight once here — never recomputed again.
        # weight is stored directly on the result dict so the iteration
        # loop can read it without recomputing.
        weight = computeDecayWeight(r["date"], today)
        if weight == 0:
            continue
 
        # Store pool and weight directly on the result dict.
        r["pool"]   = pool
        r["weight"] = weight
        results.append(r)
 
    return results

# ------------------------------------------------------------------ #
# GROUPING
# ------------------------------------------------------------------ #

# _groupResults
# Purpose: Builds two lookup dicts used throughout the iteration loop:
#          results_by_course and results_by_athlete.
# Arguments:
#           results: classified and weighted result list.
# Output: Tuple of (results_by_course, results_by_athlete).
#         results_by_course:  {course_name: [result, ...]}
#         results_by_athlete: {(athlete_id, pool): [result, ...]}
def _groupResults(results: list) -> tuple[dict, dict]:
 
    # defaultdict(list) automatically creates an empty list for new keys
    # so we can append without checking if the key exists first.
    results_by_course  = defaultdict(list)
    results_by_athlete = defaultdict(list)
 
    for r in results:
        results_by_course[r["course_name"]].append(r)
        results_by_athlete[(r["athlete_id"], r["pool"])].append(r)
 
    return results_by_course, results_by_athlete

# ------------------------------------------------------------------ #
# ATHLETE ABILITY
# ------------------------------------------------------------------ #


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
def computeAthleteAbility(performances: list, remove_outliers: bool) -> float | None:

    # Need at leat MIN_RACES performances to compute a reliable ability.
    if len(performances) < MIN_RACES:
        return None
    
    # _weightedMean is a helper that computes sum(value*weight)/sum(weights).
    # Extracted so we can call it twice (before and after outlier removal)
    # without duplicating the logic.
    mean, total_weight = _weightedMean(performances)

    # Step 2 - optionally remove outliers using previously calculated weighted mean.
    if remove_outliers:
        performances, mean = _filterOutliers(performances, mean, total_weight)

    return mean

# _weightedMean
# Purpose: Computes the weighted mean of a list of performances.
#          Returns both the mean and total_weight so callers can reuse
#          total_weight without recomputing it.
# Arguments:
#           performances: list of dicts with "adjusted_time" and "weight".
# Output: Tuple of (weighted_mean, total_weight).
def _weightedMean(performances: list) -> tuple[float, float]:
 
    total_weight = sum(p["weight"] for p in performances)
 
    if total_weight == 0:
        return 0.0, 0.0
 
    # Weighted mean = sum(value * weight) / sum(weights).
    # Gives more influence to recent races than old ones.
    mean = sum(p["adjusted_time"] * p["weight"] for p in performances) / total_weight
 
    return mean, total_weight
 
# _filterOutliers
# Purpose: Removes performances more than OUTLIER_STD_THRESHOLD standard
#          deviations from the weighted mean, then recomputes the mean
#          on the filtered set.
# Arguments:
#           performances: list of dicts with "adjusted_time" and "weight".
#           mean: pre-computed weighted mean (avoids recomputing it).
#           total_weight: pre-computed sum of weights.
# Output: Tuple of (filtered_performances, new_mean).
def _filterOutliers(performances: list, mean: float, total_weight: float) -> tuple[list, float]:
 
    # Weighted variance = sum(weight * (value - mean)^2) / sum(weights).
    # Standard deviation = sqrt(variance).
    variance = sum(
        p["weight"] * (p["adjusted_time"] - mean) ** 2
        for p in performances
    ) / total_weight
 
    std = math.sqrt(variance)
 
    # Keep only performances within OUTLIER_STD_THRESHOLD std devs of mean.
    # If std == 0 all performances are identical — keep all of them.
    filtered = [
        p for p in performances
        if std == 0 or abs(p["adjusted_time"] - mean) <= OUTLIER_STD_THRESHOLD * std
    ]
 
    # If filtering removed too many races, fall back to unfiltered.
    # Prevents dropping below MIN_RACES for athletes with few results.
    if len(filtered) < MIN_RACES:
        filtered = performances
 
    # Recompute weighted mean on filtered set.
    new_mean, _ = _weightedMean(filtered)
 
    return filtered, new_mean

# ------------------------------------------------------------------ #
# ITERATION LOOP HELPERS
# ------------------------------------------------------------------ #

# _computeAthleteAbilities
# Purpose: Computes ability for every athlete using current course
#          difficulties. Called once per iteration.
#          Key optimization: weights are already stored on each result
#          dict — we only recompute adjusted_time (which changes each
#          iteration as course_difficulties updates).
# Arguments:
#           results_by_athlete: {(athlete_id, pool): [result, ...]}
#           course_difficulties: {course_name: difficulty}
#           remove_outliers: whether to drop outliers this iteration.
# Output: {(athlete_id, pool): ability_in_seconds}
def _computeAthleteAbilities(results_by_athlete: dict,
                              course_difficulties: dict,
                              remove_outliers: bool) -> dict:
    
    # This is a dict: {(athlete_id, pool): ability_in_seconds}
    athlete_abilities = {}

    # For each athlete in each pool it computes their ability based
    # on the adjusted time(distance and difficulty) and the weight of each result.
    for (athlete_id, pool), athlete_results in results_by_athlete.items():

        # Build performances list - each entry has adjusted-time, based on
        # distance and course difficulty normalization, and weight. This
        # is built on all the athlete's results in that pool.
        performances = [
            {
                # Divide out course difficulty to get flat equivalent.
                # (1 + difficulty) converts percentage to multiplier:
                # difficulty = 0.03 means the course is 3% harder,
                # so dividing it by 1.03 gives the flat equivalent time.
                "adjusted_time": r["normalized_time"] / (1 + course_difficulties.get(r["course_name"], 0.0)),
                "weight": r["weight"]
            }
            for r in athlete_results
        ]

        ability = computeAthleteAbility(performances, remove_outliers)
        if ability is not None:
            athlete_abilities[(athlete_id, pool)] = ability

    return athlete_abilities

# _computeCourseDifficulties
# Purpose: Recomputes course difficulties from athlete abilities.
#          For each course, compares every result to what the athlete
#          was expected to run based on their ability, and averages
#          the deviations.
# Arguments:
#           results_by_course: {course_name: [result, ...]}
#           athlete_abilities: {(athlete_id, pool): ability}
#           old_difficulties: previous iteration's difficulties for damping.
# Output: New {course_name: difficulty} dict with damping applied.
def _computeCourseDifficulties(results_by_course: dict,
                                athlete_abilities: dict,
                                old_difficulties: dict) -> dict:
        
    new_difficulties = {}

    # For each course, look at every result run there, compare it to 
    # what the athlete was expected to run based on their ability,
    # and average those deviations together to get the course difficulty.
    for course_name, course_results in results_by_course.items():

        # Collect percentage deviations for results where we know
        # the athlete's ability.
        deviations = []

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

        # Count unique athletes on this course.
        n_athletes = len(set(r["athlete_id"] for r in course_results))
        
        # No usables results for this course - keep difficulty at 0.
        # Too few athletes to compute a reliable difficulty —
        # treat as flat neutral. Will be recomputed on future
        # engine runs as more data comes in.
        if not deviations or n_athletes < 20:
            # No usables results for this course - keep difficulty at 0.
            new_difficulties[course_name] = 0.0
        else:
            # Course difficulty = average deviation across all results.
            raw = sum(deviations) / len(deviations)
 
            # Damping blends old and new to prevent oscillation.
            # Without it the algorithm overshoots each iteration and
            # bounces around the true value instead of settling on it.
            # Same concept as learning rate in gradient descent.
            old = old_difficulties.get(course_name, 0.0)
            new_difficulties[course_name] = (1 - DAMPING) * old + DAMPING * raw

    return new_difficulties

# _checkConvergence
# Purpose: Computes average absolute change in course difficulties
#          between iterations. Used to decide whether to stop.
# Arguments:
#           old_difficulties: previous iteration's difficulties.
#           new_difficulties: this iteration's difficulties.
# Output: Float average change across all courses.
def _checkConvergence(old_difficulties: dict, new_difficulties: dict) -> float:
 
    if not old_difficulties:
        return 0.0
    
    # Sums over all the courses finding the change between the new difficulty
    # and the old difficulty. It adds all these up to get the total change.
    total_change = sum(
        abs(new_difficulties.get(c, 0.0) - old_difficulties.get(c, 0.0))
        for c in old_difficulties
    )
 
    return total_change / len(old_difficulties)
 

# ------------------------------------------------------------------ #
# POST-PROCESSING HELPERS
# ------------------------------------------------------------------ #

# _buildDifficultiesToSave
# Purpose: Adds n_results and n_athletes metadata to course difficulties
#          for storage. This metadata is used for confidence filtering
#          when displaying results — thin courses get flagged.
# Arguments:
#           course_difficulties: {course_name: difficulty}
#           results_by_course: {course_name: [result, ...]}
# Output: Dict in saveCourseDifficulties format.
def _buildDifficultiesToSave(course_difficulties: dict,
                              results_by_course: dict) -> dict:
 
    difficulties_to_save = {}
    
    # For each course it adds # of athletes and # of results metadata
    # on that course.
    for course_name, difficulty in course_difficulties.items():

        # Gets all results for a course.
        course_results = results_by_course[course_name]

        # Gets how many unique athletes have run a course.
        n_athletes = len(set(r["athlete_id"] for r in course_results))

        # Saves the number of results and number of athletes
        # metadata to the difficulties to save.
        difficulties_to_save[course_name] = {
            "difficulty":  difficulty,
            "n_results":   len(course_results),
            "n_athletes":  n_athletes
        }
 
    return difficulties_to_save
 

# _computePoolMeans
# Purpose: Computes the mean ability per pool. This becomes the
#          100-point anchor — an athlete at exactly the mean gets
#          a speed rating of 100.
# Arguments:
#           athlete_abilities: {(athlete_id, pool): ability_in_seconds}
# Output: {pool: mean_ability_in_seconds}
def _computePoolMeans(athlete_abilities: dict) -> dict:
 
    # Group abilities by pool.
    abilities_by_pool = defaultdict(list)
    
    # Sums over each athlete in each pool, adding their ability
    # as an entry in the dict of abilities by pool.
    for (athlete_id, pool), ability in athlete_abilities.items():
        abilities_by_pool[pool].append(ability)
 
    # Mean ability per pool — the 100-point anchor. Sums over
    # all abilities in each pool, then divides it by # of abilities
    # in each poool.
    return {
        pool: sum(abilities) / len(abilities)
        for pool, abilities in abilities_by_pool.items()
        if abilities
    }

# _buildAthleteRatings
# Purpose: Converts athlete abilities to points scale using pool means.
#          Formula: points = (pool_mean / ability) * 100.
#          Faster than average (lower seconds) → above 100.
#          Slower than average (higher seconds) → below 100.
# Arguments:
#           athlete_abilities: {(athlete_id, pool): ability}
#           pool_means: {pool: mean_ability}
#           results_by_athlete: used to count n_races per athlete.
# Output: Dict in saveAthleteRatings format.
def _buildAthleteRatings(athlete_abilities: dict,
                          pool_means: dict,
                          results_by_athlete: dict) -> dict:
 
    ratings_to_save = {}
    
    # For each athlete in a pool calculates their speed rating
    # based on their adjusted time (ability) and pool mean.
    for (athlete_id, pool), ability in athlete_abilities.items():

        pool_mean = pool_means.get(pool)

        if pool_mean is None or pool_mean == 0:
            continue
        
        # Ability is each athlete's random average adjusted time
        # based on distance and difficulty normalization. Lower
        # time = lower ability = higher speed rating.
        points  = (pool_mean / ability) * 100
        n_races = len(results_by_athlete[(athlete_id, pool)])
 
        ratings_to_save[(athlete_id, pool)] = {
            "speed_rating": round(points, 2),
            "n_races":      n_races
        }
 
    return ratings_to_save

# _buildResultRatings
# Purpose: Computes a speed rating for every individual result.
#          Same formula as athlete ratings but applied per-result
#          with no weighting or outlier removal.
# Arguments:
#           results: full classified result list.
#           course_difficulties: final converged difficulties.
#           pool_means: {pool: mean_ability}
# Output: {result_id: speed_rating}
def _buildResultRatings(results: list,
                         course_difficulties: dict,
                         pool_means: dict) -> dict:
 
    result_ratings = {}
 
    for r in results:
        pool_mean  = pool_means.get(r["pool"])
        difficulty = course_difficulties.get(r["course_name"], 0.0)
 
        if pool_mean is None or pool_mean == 0:
            continue
 
        # Adjust for course difficulty to get flat equivalent time,
        # then convert to speed rating.
        adjusted_time = r["normalized_time"] / (1 + difficulty)
        speed_rating  = (pool_mean / adjusted_time) * 100
 
        result_ratings[r["result_id"]] = round(speed_rating, 2)
 
    return result_ratings
 

# ------------------------------------------------------------------ #
# MAIN ENGINE
# ------------------------------------------------------------------ #

# runEngine
# Purpose: Orchestrates the full pipeline:
#          load → classify → group → iterate → save.
# Arguments: None.
# Output: None. Writes course difficulties, athlete ratings, and
#         per-result speed ratings to the DB.
def runEngine():

    # Today's date - used for all decay weight calculations.
    today = date.today()

    # ---- Load data ------------------------------------------------ #

    print("Loading results from database...")

    raw_results = loadResults()

    if not raw_results:
        print("No results found. Exiting.")
        return

    # ---- Classify and pre-compute weights ------------------------ #
 
    # Weights are computed ONCE here and stored on each result dict.
    # The iteration loop reads r["weight"] directly — no recomputation.
    # This eliminates ~60M redundant math.pow() calls across 15 iterations.
    results = _classifyAndWeight(raw_results, today)
    print(f"Using {len(results):,} results after classification and decay filtering")

    # ---- Group results by course and by athlete ------------------- #
    
    results_by_course, results_by_athlete = _groupResults(results)
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
    # Used to stop iterating early if no improvement after multiple iterations.
    iterations_without_improvement = 0
    
    for iteration in range(MAX_ITERATIONS):

        # On iteration 0 we don't remove outlier because athlete abilities
        # are based on raw time w/o course adjustment yet, so outliers
        # might just be hard courses.
        if iteration > 0:
            remove_outliers = True
        else: 
            remove_outliers = False

        # Save current difficulties before overwriting them in step 2.
        # _checkConvergence needs to compare this iteration against
        # the previous one, not against the all-time best.
        old_difficulties = course_difficulties.copy()


        # Step 1 — compute athlete abilities from current difficulties.
        athlete_abilities = _computeAthleteAbilities(
            results_by_athlete, course_difficulties, remove_outliers
        )

        # Step 2 — recompute course difficulties from athlete abilities.
        course_difficulties = _computeCourseDifficulties(
            results_by_course, athlete_abilities, course_difficulties
        )
            
       # Step 3 — check convergence.
        avg_change = _checkConvergence(old_difficulties, course_difficulties)
        print(f"Iteration {iteration + 1}: avg change = {avg_change:.6f}")
 
        # Track best state seen so far.
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
    
    # ---- Post-processing ---------------------------------------- #

    print("\nComputing final metadata...")
    
    # Adds number of athletes and number of results to a courses metadata
    # so we can flag low confidence later if not enough people have run it.
    difficulties_to_save = _buildDifficultiesToSave(course_difficulties, results_by_course)
 
    print("Computing pool means...")
    pool_means = _computePoolMeans(athlete_abilities)
 
    print("Computing athlete ratings...")
    ratings_to_save = _buildAthleteRatings(athlete_abilities, pool_means, results_by_athlete)
 
    print("Computing per-result speed ratings...")
    result_ratings = _buildResultRatings(results, course_difficulties, pool_means)

    print(f"Computed {len(result_ratings):,} per-result speed ratings")
    
    # ---- Save --------------------------------------------------- #

    print("\nSaving to database...")
    saveCourseDifficulties(difficulties_to_save)
    saveAthleteRatings(ratings_to_save)
    saveResultSpeedRatings(result_ratings)
 
    print("\nEngine complete.")
    print(f"Courses rated:  {len(difficulties_to_save):,}")
    print(f"Athletes rated: {len(ratings_to_save):,}")


if __name__ == "__main__":
    runEngine()


