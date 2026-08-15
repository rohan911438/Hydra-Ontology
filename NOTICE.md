# Third-party notices

## EnterpriseRAG-Bench

This project uses a small subset of
[EnterpriseRAG-Bench](https://github.com/onyx-dot-app/EnterpriseRAG-Bench)
(v1.0.0 release), a synthetic dataset released for benchmarking retrieval and
reasoning over enterprise data. Specifically: one slice each of
`github_slice_0001.zip`, `jira_slice_0001.zip`, and `slack_slice_0001.zip`,
further sampled down by `scripts/sample_dataset.py`. All content describes a
fictional company ("Redwood Inference") and fictional people — it is
synthetic, not real enterprise or personal data.

Check the dataset repository directly for its current license terms before
any non-research/non-hackathon use.

## HydraDB

[HydraDB](https://github.com/hydra-db/hydradb) is licensed under the GNU
Affero General Public License v3.0 (AGPL-3.0). This project connects to a
standalone HydraDB server over the Bolt protocol as a client; no HydraDB
source code is included, modified, or redistributed here.

## neo4j Python driver

[neo4j/neo4j-python-driver](https://github.com/neo4j/neo4j-python-driver),
used under its own license, to talk to HydraDB over Bolt (HydraDB implements
Bolt-compatible wire protocol per its own documentation).

## Gemini API

Entity/relationship extraction, entity-resolution judging, conflict
classification, and NL query routing use the Google Gemini API, subject to
Google's API terms.
