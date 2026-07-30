# dbslice-ai-connector

Connect local datasets to a remote dbsliceAI MCP server.

The connector is under initial development. The authoritative versioned wire
contract is in [`protocol/v1`](protocol/v1/README.md). The client implements
the persistent outbound WebSocket transport and the five initial dataset
operations. Production enrollment and credential handling are not yet
implemented.

Create a local environment and install the connector:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
```

Run one configured dataset alias:

```bash
export DBSLICE_CONNECTOR_CREDENTIAL='<connector credential>'
.venv/bin/dbslice-ai-connector \
  --server-url ws://127.0.0.1:3001/connector/v1 \
  --connector-instance-id ci_example001 \
  --dataset "synthetic-study=Synthetic latency study=/absolute/path/to/dataset"
```

The credential is read from the environment and sent only in the WebSocket
`Authorization` header. It is never placed in the URL or command-line
arguments. The product enrollment mechanism will provision and rotate this
credential in a later release.

The dataset root uses the existing dbsliceAI filesystem layout: configuration
at `config/config.json`, with metadata and extract paths resolved relative to
that root. Connector-local paths are removed from `getDatasetConfig` responses.

Run the protocol conformance fixtures with Python 3.11 or newer:

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```
