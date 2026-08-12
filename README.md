# dbslice-ai-connector

Connect local datasets to a remote dbsliceAI MCP server.

The connector is under initial development. The authoritative versioned wire
contract is in [`protocol/v1`](protocol/v1/README.md). The client implements
the persistent outbound WebSocket transport, the five initial dataset
operations and one-time product enrollment.

Create a local environment and install the connector:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
```

Enroll one connector installation using the short-lived token supplied by a
dbsliceAI workspace administrator:

```bash
.venv/bin/dbslice-ai-connector enroll \
  --server-url https://app.ai.dbslice.org
```

The command reads the enrollment token from
`DBSLICE_CONNECTOR_ENROLLMENT_TOKEN`, or prompts without echoing when that
variable is absent. It generates the installation ID and writes the returned
refresh credential to:

```text
~/.config/dbslice-ai-connector/credentials.json
```

On POSIX systems the directory is mode `0700` and the file is mode `0600`.
The command refuses to replace an existing credential file. Use
`--credentials-file` to select an explicit service-owned location.

The refresh credential is not yet accepted for production WebSocket sessions.
Until short-lived session authorization is implemented, the existing
development transport can be run with an explicitly supplied development
credential:

```bash
export DBSLICE_CONNECTOR_CREDENTIAL='<connector credential>'
.venv/bin/dbslice-ai-connector run \
  --server-url ws://127.0.0.1:3001/connector/v1 \
  --connector-instance-id ci_example001 \
  --dataset "synthetic-study=Synthetic latency study=/absolute/path/to/dataset"
```

The development credential is read from the environment and sent only in the
WebSocket `Authorization` header. Secrets are never placed in URLs or
command-line arguments. Product session-token exchange and refresh-credential
rotation are the next security boundary.

The dataset root uses the existing dbsliceAI filesystem layout: configuration
at `config/config.json`, with metadata and extract paths resolved relative to
that root. Connector-local paths are removed from `getDatasetConfig` responses.

Run the protocol conformance fixtures with Python 3.11 or newer:

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```
