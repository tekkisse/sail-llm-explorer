-- Synthetic SAIL-like schema for the demo. NOT real data.
-- Table and column names follow SAIL conventions so prompts and examples transfer.

CREATE SCHEMA IF NOT EXISTS sail;

CREATE TABLE sail.wdsd_ar_pers (
    alf_pe              bigint PRIMARY KEY,
    wob                 date,
    gndr_cd             smallint,
    lsoa2011_cd         varchar(9),
    wimd_2019_quintile  smallint,
    ethn_cat            varchar(20)
);

CREATE TABLE sail.wlgp_clean_gp_reg (
    alf_pe      bigint NOT NULL,
    prac_cd_pe  integer NOT NULL,
    start_date  date NOT NULL,
    end_date    date
);

CREATE TABLE sail.wlgp_gp_event_cleansed (
    alf_pe      bigint NOT NULL,
    prac_cd_pe  integer NOT NULL,
    event_cd    varchar(5) NOT NULL,
    event_val   numeric(8,2),
    event_dt    date NOT NULL
);

CREATE TABLE sail.pedw_spell (
    spell_num_pe   bigint PRIMARY KEY,
    alf_pe         bigint NOT NULL,
    admis_dt       date NOT NULL,
    disch_dt       date,
    admis_mthd_cd  varchar(2)
);

CREATE TABLE sail.pedw_diag (
    spell_num_pe  bigint NOT NULL,
    diag_num      smallint NOT NULL,
    diag_cd_1234  varchar(4) NOT NULL
);

CREATE TABLE sail.adde_deaths (
    alf_pe                          bigint PRIMARY KEY,
    dod                             date NOT NULL,
    deathcause_diag_underlying_cd   varchar(4)
);

-- Read v2 lookup (subset used by the synthetic data)
CREATE TABLE sail.lkp_read_cd (
    read_cd  varchar(5) PRIMARY KEY,
    pref_term_60 varchar(60) NOT NULL
);

INSERT INTO sail.lkp_read_cd VALUES
 ('C10F.', 'Type 2 diabetes mellitus'),
 ('C109.', 'Non-insulin dependent diabetes mellitus'),
 ('C10E.', 'Type 1 diabetes mellitus'),
 ('G20..', 'Essential hypertension'),
 ('G203.', 'Diastolic hypertension'),
 ('H33..', 'Asthma'),
 ('H333.', 'Acute exacerbation of asthma'),
 ('42W5.', 'Haemoglobin A1c level - IFCC standardised'),
 ('22K..', 'Body Mass Index'),
 ('137R.', 'Current smoker'),
 ('137S.', 'Ex smoker'),
 ('1371.', 'Never smoked tobacco'),
 ('246..', 'O/E - blood pressure reading'),
 ('9N1..', 'Seen in GP surgery');
