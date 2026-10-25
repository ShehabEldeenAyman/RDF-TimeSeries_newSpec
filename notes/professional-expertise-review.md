# Professional expertise review

Prepared 23 October 2026 from the three local project folders. This is a working evidence base for later LinkedIn and ResearchGate writing.

## Professional context

You described yourself as a PhD student at Ghent University in the KNoWS team, supervised by Pieter Colpaert, and a researcher at VITO. These affiliations are supplied by you. Your exact PhD research question, individual contribution to shared work, publications, and preferred future opportunities still need to be established.

## Central assessment

The strongest connecting theme in these repositories is **semantic interoperability for environmental time-series data, combined with applied forecasting and careful experimental evaluation**.

The work addresses a connected set of questions: How can a machine discover what a sensor series means? How can heterogeneous files retain their units, datatypes, quality information, and provenance? How can those observations be validated, queried, published, and prepared for forecasting? How can forecasting comparisons avoid misleading conclusions caused by time leakage, missingness, or unequal evaluation samples?

This is an interpretation of the project evidence, not a verified statement of your formal thesis scope or sole authorship. The distinctive professional story is the connection between data representation, working software, and empirical evaluation.

## Scope and method

The source inventory covers **363 files and 66,044 source lines**, including Python, JavaScript/React, notebook code cells, N3 rules, HTML, and CSS:

| Project | Indexed source files | Indexed source lines | Review emphasis |
| --- | ---: | ---: | --- |
| RDF-TimeSeries_newSpec | 202 | 36,372 | Evolution across demos 1–7; detailed reading of demo 7 conversion, RDF handling, validation, metadata, and browser architecture |
| Gent-Terneuzen canal | 141 | 25,316 | Acquisition-to-publication pipeline; active forecasting utilities and notebook protocols; saved result summaries; browser inference and analytics prototypes |
| Generic-Dashboard-System | 20 | 4,356 | Generic pipeline, run/artifact lifecycle, unit alignment, transformations, API/UI structure, and test coverage |

These are inventory counts, not independent contributions or measures of expertise. They include repeated demo versions and historical prototypes. The review combines a repository-wide structural scan with detailed reading of central implementation paths and representative tests; it is **not a line-by-line semantic audit of every historical file**. Generated datasets, notebook output payloads, installed dependencies, caches, model weights, and document-editing helpers were excluded from source counts. Selected Turtle metadata, SHACL shapes, mappings, and model descriptions were reviewed separately.

Two historical notebooks could not be parsed as notebook JSON: `_deprecated/ingest.ipynb` and `time_series_analysis/deprecated/convert.ipynb` in the canal project. The topology report and graph named by the two repositories' AGENTS.md files were absent at those paths, so architecture was traced from source imports, orchestration, routes, and tests.

No training, ingestion, external publication, or application test execution was performed for this assessment. Saved experimental outputs were inspected as existing artifacts, not independently reproduced. The machine-readable companion is [expertise-source-inventory.json](<C:/Code exercises/RDF-TimeSeries_newSpec/notes/expertise-source-inventory.json>).

## Evidence of expertise

### 1. Semantic modelling and time-series interoperability

The RDFTSS work explores how to describe time-series structure and meaning separately from the physical data representation. Earlier demos use snippet groups, templates, inheritance, CSVW, and embedded JSON-LD. Demo 7 moves to explicit sources and fields, with time fields, keys, datatypes, units, observed properties, sensors, features of interest, and source windows across CSV, Parquet, and embedded points.

The browser parses RDF into an N3 store and reads terms by full IRI. The backend and browser coordinate base IRIs so relative links retain their meaning. These choices show attention to RDF semantics beyond displaying Turtle as text.

Evidence: [demo 7 converter](<C:/Code exercises/RDF-TimeSeries_newSpec/tech demo/demo 7/code/TSS2RDF/tss2rdf.py:180>), [browser metadata helpers](<C:/Code exercises/RDF-TimeSeries_newSpec/tech demo/demo 7/code/webapp/frontend/src/tss.js:303>), [RDF document handling](<C:/Code exercises/RDF-TimeSeries_newSpec/tech demo/demo 7/code/webapp/frontend/src/rdf.js>), and [Parquet source metadata](<C:/Code exercises/RDF-TimeSeries_newSpec/tech demo/demo 7/metadata endpoint/waterinfo_289429042_tss2_parquet.ttl>).

Supported description: **semantic data modelling and interoperable sensor time-series representations**. The new TSS terms are explicitly experimental proposal vocabulary; the files do not establish adoption as a standard or sole ownership of the design.

### 2. Semantic data integration, validation, and publication

The canal pipeline connects data acquisition and workbook/CSV preparation with RML mapping, QUDT unit conversion, SHACL validation, N3/EYE reasoning, Fuseki ingestion, RDF-to-TSS transformation, and TREE/LDES publication. The generic project develops this into a configurable workflow with user-supplied mappings, shapes, rules, graph names, and stream settings.

The unit-conversion code uses source and target multipliers and offsets through a common reference. The generic version rejects known incompatible measurement families and preserves mapped RDF while generating a separate aligned artifact. This is concrete evidence of handling meaning and numerical interpretation together.

Evidence: [canal orchestration](<C:/Gent-Terneuzen canal/pipeline/pipeline_core.py>), [generic pipeline](<C:/Code exercises/Generic-Dashboard-System/pipeline/generic_pipeline.py>), [unit conversion](<C:/Code exercises/Generic-Dashboard-System/automating_alignments/automated_alignments.py>), and [LDES generation](<C:/Code exercises/Generic-Dashboard-System/RDF2LDES/RDFTSS2LDES.py:275>).

Supported description: **building semantic pipelines that integrate heterogeneous sensor data and make it validated, queryable, and publishable**. Generality has practical boundaries: downstream transformations still expect particular SOSA/QUDT observation structures.

### 3. Data quality and semantic correctness

The RDFTSS implementation addresses datatype lexical forms, language-tagged strings, empty values, time windows, field references, prefix independence, relative IRIs, and blank-node identity. Embedded-point validation lifts values into RDF so shapes can join the data with field metadata. It preserves invalid lexical text long enough for validation to identify it instead of silently normalizing it away.

Stored field summaries include provenance and missing-value policies; the dashboard can compare stored summaries with recomputed values. This connects metadata to verifiable data properties.

Evidence: [embedded-point lifting](<C:/Code exercises/RDF-TimeSeries_newSpec/tech demo/demo 7/code/TSS2RDF/tss2rdf.py:285>), [shared validation](<C:/Code exercises/RDF-TimeSeries_newSpec/tech demo/demo 7/code/shacl_report/shacl_report.py:125>), [embedded shapes](<C:/Code exercises/RDF-TimeSeries_newSpec/tech demo/demo 7/SHACL/embedded_shapes.ttl>), and [summary checks](<C:/Code exercises/RDF-TimeSeries_newSpec/tech demo/demo 7/code/webapp/backend/test_summaries.py>).

Supported description: **semantic validation and data-quality tooling for interpretable, reusable time series**. Metadata conformance alone does not prove that measurements are accurate; external-profile SHACL checks validate metadata rather than every external data cell.

### 4. Applied machine learning and forecasting evaluation

The active canal study targets Gent-near conductivity using four Waterinfo stations. Its blocked-backtesting code specifies five expanding training windows, separate validation and test months, common forecast origins within each input condition, and complete week-ahead trajectories at 15-minute resolution.

The implementation contains training-only imputation and scaling, explicit missingness indicators, persistence comparisons by lead time, validation-only calibration, and saved checkpoint predictions. Current classical code compares direct Ridge with PCA-XGBoost and PCA-LinearSVR; the PCA compresses future-change trajectories using training data only. Neural code builds LSTM and causal residual TCN models. The Chronos-2 runner evaluates a pretrained forecasting model, including zero-shot and validation-calibrated forecasts and interval diagnostics.

Evidence: [blocked experiment design](<C:/Gent-Terneuzen canal/time_series_analysis/blocked_backtesting.py>), [evaluation and calibration](<C:/Gent-Terneuzen canal/time_series_analysis/paper_experiment_utils.py:642>), [classical runner](<C:/Gent-Terneuzen canal/time_series_analysis/classical_blocked_backtest.py>), [neural runner](<C:/Gent-Terneuzen canal/time_series_analysis/neural_blocked_backtest.py>), [Chronos runner](<C:/Gent-Terneuzen canal/time_series_analysis/chronos_blocked_backtest.py>), and [trajectory PCA](<C:/Gent-Terneuzen canal/time_series_analysis/trajectory_pca.py>).

Supported description: **comparative environmental forecasting with classical, neural, and pretrained time-series models, emphasizing chronological evaluation and missing-data handling**. Saved results vary across folds and configurations; they do not establish universal model superiority. This review also does not establish that semantic technologies themselves improved predictive accuracy: that would require a specific controlled comparison.

### 5. Event-based temporal analysis and domain-informed features

The propagation analysis goes beyond choosing one global correlation peak. It detects conductivity-rise events, matches upstream and downstream events through chronology-preserving one-to-one alignment, estimates delay distributions, and reports reliability diagnostics and year-block bootstrap intervals. The blocked workflow selects its lag using each fold's training period and records an hourly-coverage sensitivity analysis.

The separate fixed-dimensional lag ablation holds the local inputs and feature count constant while changing the upstream lag, with a shuffled negative control. Its optional neural branch is disabled by default and should not be described as a completed neural experiment merely because the code exists.

Evidence: [event matching](<C:/Gent-Terneuzen canal/time_series_analysis/event_propagation.py:150>), [fold-specific lag selection](<C:/Gent-Terneuzen canal/time_series_analysis/blocked_backtesting.py:178>), and [lag ablation](<C:/Gent-Terneuzen canal/time_series_analysis/Fixed_Dimensional_Lag_Ablation.ipynb>).

Supported description: **event-based sensor analysis and testing temporal feature hypotheses for forecasting**. These are empirical conductivity-delay estimates, not validated measurements of water-mass travel time or evidence of hydrodynamic modelling expertise.

### 6. Research software and usable tools

The projects include React/FastAPI dashboards, query interfaces, RDF inspection and export, SQL over data and metadata, stage feedback, artifact downloads, isolated run directories, downstream-result invalidation, and resumable modelling jobs. The converter writes external data in batches and preserves blank-node identity across batches. Tests inspect RDF terms and values rather than relying only on serialized text.

Browser-side TensorFlow.js inference and LDES client code provide an additional prototype thread: consuming linked data and running an existing forecasting model in the browser. An MLDCAT-AP description records a model, intended use, files, and limitations.

Evidence: [run lifecycle](<C:/Code exercises/Generic-Dashboard-System/pipeline/run_store.py:135>), [pipeline API](<C:/Code exercises/Generic-Dashboard-System/pipeline/playground_server.py>), [converter batching](<C:/Code exercises/RDF-TimeSeries_newSpec/tech demo/demo 7/code/TSS2RDF/tss2rdf.py:602>), [browser inference](<C:/Gent-Terneuzen canal/frontend/src/components/EdgeDeepLearning.jsx:167>), and [model description](<C:/Gent-Terneuzen canal/MLDCAT-AP/MLDCAT-AP.ttl>).

Supported description: **developing research software that connects semantic data processing, analysis, and interactive exploration**. Prototype implementations and local tests do not establish production operation, cloud deployment, or enterprise-scale reliability.

## Working professional narrative

The evidence supports three connected layers of the same research story:

1. **Meaning:** representing what sensor observations measure, where they come from, and how they should be interpreted.
2. **Interoperability:** converting, validating, querying, and publishing observations across formats and systems.
3. **Use:** investigating environmental forecasting with controlled temporal evaluation.

A provisional plain-language statement for discussion is:

> I work on making environmental sensor time series easier to interpret, integrate, validate, and use in forecasting. My research combines semantic technologies and linked data with time-series analysis and machine learning, supported by practical research software.

Your PhD and VITO roles can anchor that statement once the exact thesis scope and your contribution wording are confirmed. The code supports a clear technical identity; it cannot supply your motivations, research ownership, career goals, or publication record.

## Material for later bios and posts

The following are content hypotheses derived from the repositories, not finished posts or a platform growth strategy:

| Topic | Concrete project evidence | Suitable angle |
| --- | --- | --- |
| A time-series value needs more than a timestamp | Field metadata, sensors, units, observed properties | Explain what a reusable sensor observation must communicate |
| One meaning across CSV, Parquet, and embedded points | Demo 7 source/field model and shared conversion | Explain the distinction between data meaning and storage format |
| A unit label can silently change an analysis | QUDT source/target conversion and regression checks | Walk through a small synthetic conductivity example |
| What SHACL can establish about a time series | Metadata shapes and lifted embedded-point checks | Show one valid case, one reported violation, and the limits of validation |
| Why persistence belongs in a forecasting comparison | Per-lead skill and held-out chronological evaluation | Explain why a visually convincing forecast may add little predictive value |
| Missingness changes the experiment | Complete-case and missingness-aware conditions | Explain why metrics on different origin sets are not directly paired |
| A lag estimate needs a distribution and diagnostics | Event matches, IQR, sensitivity and reliability flags | Discuss uncertainty without presenting a universal transport delay |
| From one case study to reusable research tooling | Canal pipeline and generic studio | Explain which assumptions can be configured and which remain fixed |
| Bringing linked data to browser inference | LDES client and TensorFlow.js prototype | Share a prototype walkthrough with its input and model limitations |

For academic writing, each project should eventually be connected to its research question, your contribution, experimental evidence, collaborators, and any verified paper/software release. For public examples, use synthetic or publishable data; the RDFTSS documentation explicitly identifies an industrial dataset as NDA-derived.

## Information needed before final public wording

- Your central PhD research question and how the three projects contribute to it.
- Which designs, implementations, and experiments you lead, and which are shared contributions.
- Papers, preprints, talks, software releases, and identifiers that can substantiate the research narrative.
- The audiences and opportunities you want to reach.
- Your existing LinkedIn and ResearchGate bios, when you are ready to work on them.

Do not infer publications from placeholder bibliographic records, formal standardization from experimental vocabulary, calibrated guarantees from diagnostic intervals, or real-world impact from a working demonstration.
