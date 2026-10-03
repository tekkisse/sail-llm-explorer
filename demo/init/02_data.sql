-- Deterministic synthetic data generator (about 20,000 people). NOT real data.
SELECT setseed(0.42);

-- People ---------------------------------------------------------------------
INSERT INTO sail.wdsd_ar_pers
SELECT
    1000000 + g                                                         AS alf_pe,
    CASE WHEN random() < 0.005 THEN DATE '1900-01-01'                   -- placeholder value, as seen in real feeds
         ELSE date_trunc('week', DATE '1930-01-01' + (random() * 30000)::int)::date
    END                                                                 AS wob,
    CASE WHEN random() < 0.002 THEN 9 WHEN random() < 0.5 THEN 1 ELSE 2 END AS gndr_cd,
    'W0' || lpad((1000000 + (random() * 1909)::int)::text, 7, '0')      AS lsoa2011_cd,
    1 + floor(random() * 5)::int                                        AS wimd_2019_quintile,
    CASE WHEN random() < 0.40 THEN NULL
         ELSE (ARRAY['White','White','White','White','White','White','Asian','Black','Mixed','Other'])[1 + floor(random() * 10)::int]
    END                                                                 AS ethn_cat
FROM generate_series(1, 20000) g;

-- GP registrations (observation windows) ---------------------------------------
INSERT INTO sail.wlgp_clean_gp_reg
SELECT p.alf_pe,
       1 + floor(random() * 120)::int,
       s.start_date,
       CASE WHEN random() < 0.15 THEN s.start_date + (365 + random() * 4000)::int ELSE NULL END
FROM sail.wdsd_ar_pers p
CROSS JOIN LATERAL (
    SELECT GREATEST(CASE WHEN p.wob = DATE '1900-01-01' THEN DATE '2000-01-01' ELSE p.wob END,
                    DATE '2000-01-01') + (random() * 3000)::int AS start_date
) s;

-- Condition flags (working table) ---------------------------------------------
CREATE TEMP TABLE cond AS
SELECT p.alf_pe, r.prac_cd_pe, r.start_date,
       COALESCE(r.end_date, DATE '2024-12-31') AS obs_end,
       LEAST(GREATEST(EXTRACT(YEAR FROM age(DATE '2024-01-01', p.wob)) - 30, 0) / 50.0, 1) AS age_factor,
       p.wimd_2019_quintile AS q,
       random() AS r_dm, random() AS r_htn, random() AS r_ast, random() AS r_smk
FROM sail.wdsd_ar_pers p
JOIN sail.wlgp_clean_gp_reg r USING (alf_pe)
WHERE p.wob <> DATE '1900-01-01';

ALTER TABLE cond ADD COLUMN has_dm  boolean, ADD COLUMN has_htn boolean, ADD COLUMN has_ast boolean;
UPDATE cond SET
    has_dm  = r_dm  < 0.02 + 0.12 * age_factor + 0.01 * (5 - q),
    has_htn = r_htn < 0.04 + 0.35 * age_factor + 0.01 * (5 - q),
    has_ast = r_ast < 0.10;

-- GP events -------------------------------------------------------------------
-- Diagnoses
INSERT INTO sail.wlgp_gp_event_cleansed (alf_pe, prac_cd_pe, event_cd, event_val, event_dt)
SELECT alf_pe, prac_cd_pe,
       CASE WHEN random() < 0.6 THEN 'C10F.' ELSE 'C109.' END, NULL,
       start_date + (random() * GREATEST(obs_end - start_date, 1))::int
FROM cond WHERE has_dm;

INSERT INTO sail.wlgp_gp_event_cleansed (alf_pe, prac_cd_pe, event_cd, event_val, event_dt)
SELECT alf_pe, prac_cd_pe, 'G20..', NULL, start_date + (random() * GREATEST(obs_end - start_date, 1))::int
FROM cond WHERE has_htn;

INSERT INTO sail.wlgp_gp_event_cleansed (alf_pe, prac_cd_pe, event_cd, event_val, event_dt)
SELECT alf_pe, prac_cd_pe, 'H33..', NULL, start_date + (random() * GREATEST(obs_end - start_date, 1))::int
FROM cond WHERE has_ast;

-- HbA1c results for people with diabetes (mmol/mol)
INSERT INTO sail.wlgp_gp_event_cleansed (alf_pe, prac_cd_pe, event_cd, event_val, event_dt)
SELECT c.alf_pe, c.prac_cd_pe, '42W5.', round((48 + random() * 40)::numeric, 0),
       c.start_date + (random() * GREATEST(c.obs_end - c.start_date, 1))::int
FROM cond c CROSS JOIN LATERAL generate_series(1, 1 + floor(random() * 8)::int) k
WHERE c.has_dm;

-- BMI for about 60% of people, with a few implausible values (data quality)
INSERT INTO sail.wlgp_gp_event_cleansed (alf_pe, prac_cd_pe, event_cd, event_val, event_dt)
SELECT alf_pe, prac_cd_pe, '22K..',
       CASE WHEN random() < 0.003 THEN 999 ELSE round((19 + random() * 18 + CASE WHEN has_dm THEN 4 ELSE 0 END)::numeric, 1) END,
       start_date + (random() * GREATEST(obs_end - start_date, 1))::int
FROM cond WHERE random() < 0.6;

-- Smoking status for about 75% of people
INSERT INTO sail.wlgp_gp_event_cleansed (alf_pe, prac_cd_pe, event_cd, event_val, event_dt)
SELECT alf_pe, prac_cd_pe,
       CASE WHEN r_smk < 0.12 + 0.03 * (5 - q) THEN '137R.' WHEN r_smk < 0.45 THEN '137S.' ELSE '1371.' END,
       NULL, start_date + (random() * GREATEST(obs_end - start_date, 1))::int
FROM cond WHERE random() < 0.75;

-- Routine consultations
INSERT INTO sail.wlgp_gp_event_cleansed (alf_pe, prac_cd_pe, event_cd, event_val, event_dt)
SELECT c.alf_pe, c.prac_cd_pe, '9N1..', NULL,
       c.start_date + (random() * GREATEST(c.obs_end - c.start_date, 1))::int
FROM cond c CROSS JOIN LATERAL generate_series(1, 1 + floor(random() * 6)::int) k;

-- Hospital spells (PEDW) --------------------------------------------------------
CREATE TEMP SEQUENCE spell_seq START 5000000;
CREATE TEMP TABLE spells AS
SELECT nextval('spell_seq') AS spell_num_pe, c.alf_pe, c.has_dm, c.has_htn, c.has_ast,
       DATE '2005-01-01' + (random() * 7300)::int AS admis_dt
FROM cond c CROSS JOIN LATERAL generate_series(1, floor(random() * (1 + 3 * c.age_factor))::int) k;

INSERT INTO sail.pedw_spell
SELECT spell_num_pe, alf_pe, admis_dt,
       CASE WHEN random() < 0.01 THEN NULL ELSE admis_dt + floor(random() * 14)::int END,
       CASE WHEN random() < 0.6 THEN '21' ELSE '11' END
FROM spells;

INSERT INTO sail.pedw_diag
SELECT spell_num_pe, 1,
       CASE WHEN has_dm AND random() < 0.15 THEN 'E119'
            WHEN has_htn AND random() < 0.15 THEN 'I10X'
            WHEN has_ast AND random() < 0.20 THEN 'J459'
            ELSE (ARRAY['I219','J189','S720','N390','K359','R074','I48X','J440'])[1 + floor(random() * 8)::int]
       END
FROM spells;

INSERT INTO sail.pedw_diag
SELECT spell_num_pe, 2, CASE WHEN has_dm THEN 'E119' ELSE 'I10X' END
FROM spells WHERE (has_dm OR has_htn) AND random() < 0.5;

-- Deaths ----------------------------------------------------------------------
INSERT INTO sail.adde_deaths
SELECT alf_pe,
       LEAST(obs_end, DATE '2024-12-31') - floor(random() * 365)::int,
       (ARRAY['I219','C349','J440','F03X','I64X','E119'])[1 + floor(random() * 6)::int]
FROM cond
WHERE random() < 0.02 + 0.10 * age_factor;

DROP TABLE spells; DROP TABLE cond;

CREATE INDEX ON sail.wlgp_gp_event_cleansed (event_cd);
CREATE INDEX ON sail.wlgp_gp_event_cleansed (alf_pe);
CREATE INDEX ON sail.pedw_spell (alf_pe);
CREATE INDEX ON sail.pedw_diag (diag_cd_1234);
ANALYZE;
