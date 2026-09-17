Xc-predictor/Racecast.co by Tadhg Murray (xc/tf athlete for Tufts University):

This contains all the code for a website project that displays xc/tf races, rates them, and predicts future results. It was built in these steps:

1. The results scraper - This is the part where we got all of the data, using HTML and API scraping. We got results data, race data, athletes data,
   school data, and venue data. This was over 200 GB of data, including over 200 million results, 1 million meets, and 60? million athletes. There
   was a lot of VPN rotation and trying to avoid Cloudflare here. Got a pretty good thing going if someone wants to copy it.

2. The weather scraper - In this part we scraped/downloaded ERA5 data for our venues at different dates. We went in about 25 x 25 km blocks,
   taken from Earthmover's public Icechunk mirror.

3. Spline Correction fitters - In this part we took the data and basically modeled it all out to correct for track geometry, weather, era, and
   distance to get a normalized 5k time we could use for the engine.
   Distance - Took the log of people running different distances within 21 days (to soften fitness effects) from both angles (each distance first)
   to fit a spline.
   Geometry - At indoor tracks of each type and track lengths of each type, look at the average normalized time someone would run compared to
   another venue within 21 days (fitness effects).
   Era - Look at how much people improve YOY. Take this curve and find its derivative to find what growth changes with time. Combine this with
   the time of the top x% every year to see the rise of that, to fit the era curve.
   Weather - Look how different weather affects normalized time just generally over the corpus, smooth the curve so there's not too many knots.
   Do this after the first normalization so we can accurately compare.

4. The ratings engine - While I had many different conceptions about what would be the hardest thing in this project
   (and after spending way too long scraping results, thought it would be that), this was easily the hardest. In this engine, we're finding two things: 
   course difficulty and an ability rating. We went through a lot of iterations (will discuss later), before ending up on a more bracketed approach a la Malcolm Slaney (tyvm).
   This just takes normalized times, and for each person, looks at how their normalized time at one course compares to another course, accounting for fitness gain
   (generally, with a fitness curve). There are a couple more bells and whistles (shrinkage, tilt where a course difficulty is not the same for different
   ability thresholds, etc.), but that's the main idea. Rating is % better than the average hser (or whatever pool we're in college, ms, etc.). Diifuclty is %
   harder/easier than a track.

5. Prediction model - a transformer model that looks at previous normalized time for races, as well as a slew of other features, to predict what someone will
   run at a later race (not necessarily their next race! We account for end of season/pre-season rankings!). Trained using a RunPod instance.

6. Website. Html/CSS/JS. Just wiring all the backend to the website. Tried my best to make it look nice (xc websites are stuck so far in the past it is insane)
   and I will continue working on it.

Other honorable mentions: All DB stuff used Postgres, except for initially using SQLite. Used Mullvad vpn to rotate vpn. Used multiple VMs to try to speed up
scraping (more on this later), and spent way too much money on it. Backend is hosted on ReliableSite (my mother nearly had a heart attack upon hearing the name
LOL). There is an insane amount of slop in this code, especially the documentation (I was running out of time before xc season, my bad). I'll clean it up over
the coming months, and this will hopefully eventually become a no-AI-written code project. There was so much data cleaning done between the rating engine and
the model. Grades, distances, and times are wrong for so many xc/tf races, and they are very hard to fix/find automatically, and way too big to find manually.

What I learned/would change:

1. CHECK ALL API ENDPOINTS BEFORE SCRAPING ANYTHING. I spent a month scraping individual track events, only to find a compiled results page and API. To expand
   on this, I should also have checked specifically what data I wanted. I spent the time thinking, okay, what is needed, rather than looking at all the data
   in the API to store other useful things. This caused me to need to rescrape about twice.

   Another related issue is the schema. The schema for this project is patchwork. It just became a thing of adding more and more things, some overlapping and having
   too many tables that need to link together, when if I had just really thought through the schema and looked at the API at the start, I could have gotten
   the scraping and the data storage immediately after to go so much more smoothly. Honestly, I think maybe I should have started at the website and reverse-
   engineered it all because it would have made it clear what I needed.

   Printing out values for scraping is key for debugging. Printing out the shape and content of your data as it goes through the pipeline through the storage
   is very useful in diagnosing where it goes wrong.

   For HTML scraping specifically, the big things to check out are whether the data is actually being parsed correctly. I did see some anti-scraping measures (such as
   fake times) trying to stop bots from scraping. Checking the page and making sure the parser looked good, then actually testing the parses on a couple
   hundred example pages would have done me better. For example, we still have data where the athletes are times, the times are schools, etc. (although this is rare).
   

3. Honestly, the weather went pretty good, no complaints.

4. This went pretty well too. I think the main things I would've changed here are looking at my inputs, making sure they actually looked sane, because the
   outputs just kind of fall out when they are sane (turns out Solarflare for the dataset I used is like 1000x). I also would've used a fitness curve here
   immediately if I was going through this again.

5. So many things went wrong here. We started with an iterative convergence engine ping-ponging from difficulty to ability. This did not go well.
   I noticed early season races were getting crazy difficulties, at which point I noticed that removing fitness from difficulty would probably be a good
   idea, and the fitness/rust curve was born. Then it just still wasn't right, it was way underrating tf, and the obvious reason became that the engine
   had no way to know tf was easier and that people were fitter. So we needed a per-sport offset, which had a lot of issues still but was much better.

   I also noticed that there was this one Washington race with a high difficulty producing crazy ratings. What I ended up figuring out was tilt, which is that
   these pretty good people were being helped too much by the difficulty, because difficulty didn't hurt them as much as it was presumed. So we tilted their ratings
   down based on ability and difficulty. Eventually, I also noticed that this race specifically had become faster, as they had changed courses (this causes much pain
   in the system), so we went to a kind of 3-year difficulty overlap thing. Also, some courses have rain courses (MT. SAC), and they got crazy ratings there.
   Because there were so few here, I just manually set a new course myself.

   I ended up not really liking the iterative convergence, and with much research went to a global solve, then a joint solve, then our current bracketed solve
   like Malcolm Slaney/LACCTIC. This was the best, but you can still tell it's off. I'm gonna keep working on it.

   The two other issues during this time were pool and corrupt distances/times for races. The wrong pool would mean people were rated in the wrong pool, and since
   for some reason putting someone's pro year as their grade is common, there would be msers with a 200 rating running sub-13! I tried so many things to fix
   the pools. I tried school levels, a level graph (connecting races to races and such), correcting any grades not in line with the rest of their legit season
   (like a senior in HS being put as a college sr), nuking anybody that didn't have enough grades to be trustworthy or didn't have a majority grade. I tried
   a pro graph starting from Diamond League races. These worked, but they ended up removing too many people. In the end, I got the pool data from an external
   source and used that to corroborate someone's grade (if they don't match, then they are either nuked or put into their school's pool). Then for clubs which
   can have either mostly pros or mostly msers, I put: if there's any pros here, nuke everybody because any "msers" there are probably just the 6th pro year
   example I said earlier. These worked.

   The other issues were corrupt distances/times. Distances were really hard to find, because even when a race is mislabelled, some people will still run quite
   bad/good, making it hard to tell. Also, there were mixed divisions with multiple race distances, and those were impossible to diagnose. Corrupt result rows
   weren't too bad because if there were a certain sigma away from the mean, they were obviously fake, although this was overzealous with college runners
   and I am still dealing with that. I tried to do the same thing for distances too and to correct
   the distances as such, but I found the corrections would often be wrong and create really really good rating for no reason, epecially in mixed divs. I ended
   up only allowing a correction to make a race slower, and eventually decided no more corrections and just do this rule: if anybody in a race runs a WR or
   a pool WR, that entire race is not rated. Since WRs are quite common when teh distance is wrong, this worked very well and really goes to show the simplest
   solution is often best.

   If I were to do this specific section again, I would research as much as possible the best method before writing anything, and I would look at similar
   systems like LACCTIC. I would also just really try to think through the issues more, and give the naive solution a try before doing anything too crazy.
   At this point I definitely got caught up in myself and felt like this was impossible, but I just really needed to give myself some time to think out a
   solution, and if that didn't work, just try the naive solution.

6. This went pretty well (and also is still going on oopsy), so no comments yet.

7. This went great; I was really happy with how the website looks and how it went. I think the biggest issue was just loading times. I had many issues
   with the loading times becoming insane during the pipeline or just being really slow otherwise. A big thing I have taken out of this about DBs, which
   applies to every section here, is to always explain your queries when you write them, and also have indices ready if you need them. When doing the pipeline
   which updates the DB, a big mistake I made was not having the indices first, making the website insanely slow while the pipeline went through multiple steps without
   indices before finally adding them. Indices are so important, and I really should have planned out creating them and, just my queries in general, better.

The main things I've taken away from this project are planning and system design. The hardest part of systems like these is the data decisions. How will
you get the data, how will you store it, what data are you even getting, how can you access it, how fast is it to access, do you need indices to speed this up,
etc. If I were to do this again, I'd plan that all out first, start the data collection (and make sure it parses correctly and your taking all you need!), 
and then start coding the rest (although cleaning the data before coding might have been more useful and worthwhile if I had some foresight). 

(I didn't even talk about the elevation backfill or the gps backfill or the id merging across scraping sources, or the race deduplicating or
the school logo backfill or the school backfill itself. Sometimes I really feel quite useless when examining this project and feel some imposter
syndrome when looking at other people, but I did do a lot of work on this project and I feel I have grown a lot and learned a lot (and made a lot)).
