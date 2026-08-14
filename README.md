# dbslice-ai-connector

Connect local datasets to a remote dbsliceAI MCP server.

## How it works

```text
configured dataset directory
        ↓
filesystem dataset provider
        ↓
outbound authenticated WebSocket
        ↓
hosted dbsliceAI tools
```

The connector runs beside the data and makes only explicitly configured
dataset roots available. It opens an outbound connection to dbsliceAI; no
inbound port or public file server is required. dbsliceAI requests a small set
of metadata and extract operations over that connection. Connector-local file
paths are never included in the returned dataset data.

The main Python modules are deliberately few:

| Module | Responsibility |
|---|---|
| `__main__.py` | Command-line parsing and process startup |
| `dataset_provider.py` | Reads only from configured dataset directories |
| `client.py` | WebSocket connection, reconnects and operation dispatch |
| `enrollment.py` | One-time registration with a dbsliceAI workspace |
| `credentials.py` | Private credential-file storage and crash-safe rotation |
| `session_authorization.py` | Exchange a refresh credential for one connection token |
| `hosted_service_http.py` | Shared HTTP handling for enrollment and authorization |
| `protocol_validation.py` | Validate protocol messages and payload fingerprints |

The files under [`protocol/v1`](protocol/v1/README.md) describe the messages
exchanged with dbsliceAI and provide examples used by the tests. They are not
additional connector services.

## Install and run

Create a local environment and install the connector:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e .
```

The connector uses Python's standard verified TLS configuration. If a
Python.org macOS installation reports `CERTIFICATE_VERIFY_FAILED`, run the
`Install Certificates.command` supplied with that Python installation. As a
temporary diagnostic workaround, point `SSL_CERT_FILE` at an existing trusted
certificate-authority (CA) bundle such as `/etc/ssl/cert.pem`. Do not disable
certificate verification.

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

Run the enrolled connector using the stored identity and one or more explicit
dataset roots:

```bash
.venv/bin/dbslice-ai-connector run \
  --dataset "pilot=Pilot dataset=/absolute/path/to/dataset"
```

Credential use has four steps:

1. The connector privately prepares its next random refresh credential.
2. It exchanges the current credential for a one-use, short-lived connection
   token while sending only the hash of the next credential.
3. It saves the accepted next credential and uses the connection token to open
   the WebSocket.
4. dbsliceAI rejects reuse of an invalidated credential and revokes the
   connector if the reuse conflicts with the expected rotation.

A small private recovery file makes retrying the same rotation safe when an
HTTP response is lost. Recovery is limited to five minutes. The credential
values are never placed in URLs, command-line arguments or logs.

The earlier development transport remains available with an explicitly
supplied development credential:

```bash
export DBSLICE_CONNECTOR_CREDENTIAL='<connector credential>'
.venv/bin/dbslice-ai-connector run \
  --server-url ws://127.0.0.1:3001/connector/v1 \
  --connector-instance-id ci_example001 \
  --dataset "synthetic-study=Synthetic latency study=/absolute/path/to/dataset"
```

The development credential is read from the environment and sent only in the
WebSocket `Authorization` header. Secrets are never placed in URLs or
command-line arguments. The product path does not accept static development
credentials.

The dataset root uses the existing dbsliceAI filesystem layout: configuration
at `config/config.json`, with metadata and extract paths resolved relative to
that root. The complete public format is described in the
[canonical dataset specification](https://github.com/dbslice/dbslice-ai-sample-data/blob/main/DATASET_FORMAT.md).
Connector-local paths are not sent to dbsliceAI clients.

For a production-shaped first connection check, download and verify the
versioned axial compressor sample. GitHub access is required while the sample
repository is private:

```bash
gh release download v1.0.0 \
  --repo dbslice/dbslice-ai-sample-data \
  --pattern 'dbslice-ai-sample-data-1.0.0*' \
  --pattern 'SHA256SUMS'
shasum -a 256 -c SHA256SUMS
unzip dbslice-ai-sample-data-1.0.0.zip
```

Then register the extracted dataset root:

```bash
dbslice-ai-connector run \
  --dataset "axial-compressor-sample=Axial compressor sample=/absolute/path/to/dbslice-ai-sample-data-1.0.0"
```

## Security boundaries

The connector:

- exposes only dataset roots explicitly supplied by its operator
- rejects paths that resolve outside those roots
- keeps persistent credentials in a private file
- sends credentials only to the enrolled server origin over HTTPS
- uses short-lived, one-use tokens for WebSocket connections
- validates protocol messages and bounds payload sizes

The process necessarily has the filesystem permissions of the account running
it. Run it as a dedicated, minimally privileged user when possible, and grant
that account read access only to the datasets it should expose. Never include
real credentials, enrollment tokens or private dataset contents in bug reports.

## Development

Run all connector and protocol tests with Python 3.11 or newer:

```bash
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```
