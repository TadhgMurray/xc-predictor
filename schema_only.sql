--
-- PostgreSQL database dump
--

\restrict TTGlHS8sPjgGVFtMuW5Vi6EDZBVsidW1T8A2yrQvMTlBVuRjNihgOs3wlmKi8np

-- Dumped from database version 17.7 (Ubuntu 17.7-3.pgdg24.04+1)
-- Dumped by pg_dump version 18.4

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET transaction_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: repmgr; Type: SCHEMA; Schema: -; Owner: postgres
--

CREATE SCHEMA repmgr;


ALTER SCHEMA repmgr OWNER TO postgres;

--
-- Name: repmgr; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS repmgr WITH SCHEMA repmgr;


--
-- Name: EXTENSION repmgr; Type: COMMENT; Schema: -; Owner: 
--

COMMENT ON EXTENSION repmgr IS 'Replication manager for PostgreSQL';


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: athlete_ratings; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.athlete_ratings (
    athlete_id bigint NOT NULL,
    pool text NOT NULL,
    speed_rating real,
    n_races integer,
    last_updated text
);


ALTER TABLE public.athlete_ratings OWNER TO postgres;

--
-- Name: athletes; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.athletes (
    athlete_id bigint NOT NULL,
    first_name text,
    last_name text,
    gender text,
    school text NOT NULL
);


ALTER TABLE public.athletes OWNER TO postgres;

--
-- Name: course_difficulties; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.course_difficulties (
    course_name text NOT NULL,
    difficulty real,
    n_results integer,
    n_athletes integer,
    last_updated text
);


ALTER TABLE public.course_difficulties OWNER TO postgres;

--
-- Name: meet_queue; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.meet_queue (
    meet_id bigint NOT NULL,
    sport text NOT NULL,
    scraped integer DEFAULT 0
);


ALTER TABLE public.meet_queue OWNER TO postgres;

--
-- Name: meets; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.meets (
    div_id bigint NOT NULL,
    meet_id bigint,
    meet_name text,
    course_name text,
    distance real,
    gps_lat real,
    gps_long real,
    state text,
    location_id bigint
);


ALTER TABLE public.meets OWNER TO postgres;

--
-- Name: meets_tf; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.meets_tf (
    div_id bigint NOT NULL,
    meet_id bigint,
    meet_name text,
    event_short text,
    event_id bigint NOT NULL,
    distance_meters real,
    gps_lat real,
    gps_long real,
    state text,
    is_indoor integer DEFAULT 0,
    location_id bigint
);


ALTER TABLE public.meets_tf OWNER TO postgres;

--
-- Name: results; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.results (
    result_id bigint NOT NULL,
    athlete_id bigint,
    meet_id bigint,
    div_id bigint,
    time_seconds real,
    grade text,
    date text,
    normalized_time real,
    speed_rating real,
    school text,
    school_source text
);


ALTER TABLE public.results OWNER TO postgres;

--
-- Name: results_tf; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.results_tf (
    result_id bigint NOT NULL,
    athlete_id bigint,
    meet_id bigint,
    div_id bigint,
    event_id bigint,
    event_short text,
    time_seconds real,
    grade text,
    date text,
    is_relay integer DEFAULT 0,
    normalized_time real,
    speed_rating real,
    school text,
    school_source text
);


ALTER TABLE public.results_tf OWNER TO postgres;

--
-- Name: tf_recovery_queue; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.tf_recovery_queue (
    meet_id bigint NOT NULL,
    event_short text NOT NULL,
    div_id bigint NOT NULL,
    scraped integer DEFAULT 0
);


ALTER TABLE public.tf_recovery_queue OWNER TO postgres;

--
-- Name: tf_scraped_events; Type: TABLE; Schema: public; Owner: postgres
--

CREATE TABLE public.tf_scraped_events (
    meet_id bigint NOT NULL,
    event_short text NOT NULL,
    div_id bigint NOT NULL,
    scraped_at text
);


ALTER TABLE public.tf_scraped_events OWNER TO postgres;

--
-- Name: athlete_ratings athlete_ratings_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.athlete_ratings
    ADD CONSTRAINT athlete_ratings_pkey PRIMARY KEY (athlete_id, pool);


--
-- Name: athletes athletes_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.athletes
    ADD CONSTRAINT athletes_pkey PRIMARY KEY (athlete_id, school);


--
-- Name: course_difficulties course_difficulties_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.course_difficulties
    ADD CONSTRAINT course_difficulties_pkey PRIMARY KEY (course_name);


--
-- Name: meet_queue meet_queue_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.meet_queue
    ADD CONSTRAINT meet_queue_pkey PRIMARY KEY (meet_id, sport);


--
-- Name: meets meets_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.meets
    ADD CONSTRAINT meets_pkey PRIMARY KEY (div_id);


--
-- Name: meets_tf meets_tf_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.meets_tf
    ADD CONSTRAINT meets_tf_pkey PRIMARY KEY (div_id, event_id);


--
-- Name: results results_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.results
    ADD CONSTRAINT results_pkey PRIMARY KEY (result_id);


--
-- Name: results_tf results_tf_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.results_tf
    ADD CONSTRAINT results_tf_pkey PRIMARY KEY (result_id);


--
-- Name: tf_recovery_queue tf_recovery_queue_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.tf_recovery_queue
    ADD CONSTRAINT tf_recovery_queue_pkey PRIMARY KEY (meet_id, event_short, div_id);


--
-- Name: tf_scraped_events tf_scraped_events_pkey; Type: CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.tf_scraped_events
    ADD CONSTRAINT tf_scraped_events_pkey PRIMARY KEY (meet_id, event_short, div_id);


--
-- Name: idx_results_athlete_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_results_athlete_id ON public.results USING btree (athlete_id);


--
-- Name: idx_results_meet_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_results_meet_id ON public.results USING btree (meet_id);


--
-- Name: idx_results_normalized_time; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_results_normalized_time ON public.results USING btree (normalized_time);


--
-- Name: idx_results_tf_athlete_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_results_tf_athlete_id ON public.results_tf USING btree (athlete_id);


--
-- Name: idx_results_tf_meet_id; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_results_tf_meet_id ON public.results_tf USING btree (meet_id);


--
-- Name: idx_results_tf_normalized_time; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_results_tf_normalized_time ON public.results_tf USING btree (normalized_time);


--
-- Name: idx_tf_recovery_scraped; Type: INDEX; Schema: public; Owner: postgres
--

CREATE INDEX idx_tf_recovery_scraped ON public.tf_recovery_queue USING btree (scraped);


--
-- Name: results results_athlete_school_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.results
    ADD CONSTRAINT results_athlete_school_fkey FOREIGN KEY (athlete_id, school) REFERENCES public.athletes(athlete_id, school);


--
-- Name: results_tf results_tf_athlete_school_fkey; Type: FK CONSTRAINT; Schema: public; Owner: postgres
--

ALTER TABLE ONLY public.results_tf
    ADD CONSTRAINT results_tf_athlete_school_fkey FOREIGN KEY (athlete_id, school) REFERENCES public.athletes(athlete_id, school);


--
-- PostgreSQL database dump complete
--

\unrestrict TTGlHS8sPjgGVFtMuW5Vi6EDZBVsidW1T8A2yrQvMTlBVuRjNihgOs3wlmKi8np

