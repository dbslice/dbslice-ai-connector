# Connector Protocol V1

This directory is the authoritative connector wire contract.

## Framing

- Each WebSocket text frame contains exactly one UTF-8 JSON message.
- WebSocket binary frames are not valid in protocol V1.
- Image and GLB payload bytes use standard padded base64 inside the JSON
  `operation.success` message.
- Line and embedding payloads use RFC 8785 JSON Canonicalization Scheme (JCS).
  Their `encodedSizeBytes` value is the byte length of that canonical form,
  and their fingerprint is computed over the same bytes.
- A binary payload is limited to 16 MiB decoded and 22,369,624 bytes base64
  encoded. A complete incoming WebSocket frame is limited to 24 MiB.
- V1 does not chunk payloads or use upload targets. Measurements from the
  latency spike decide whether a later protocol version needs either.

## Messages

`protocol.schema.json` defines:

- the five dataset operation requests and their success/error responses
- session introduction and acceptance
- dataset-alias advertisement
- heartbeat ping/pong
- operation cancellation and cancellation results

Public dataset metadata may include `dataset.curatedReferences`. The connector
loads those entries from the dataset's private manifest declaration before
returning `getDatasetConfig`; manifest paths and local documents never cross the
wire. This uses the existing JSON-valued `dataset` object and does not add a
protocol operation.

The authenticated WebSocket supplies the connector identity. User ownership
is resolved by the hosted service and never crosses the wire in messages.

Operation cancellation is best effort. An accepted cancellation means that
the connector will not deliberately continue the operation, but the server
must still reject any late or duplicate response for that request.

## Compatibility

Both Python and JavaScript validate the fixtures in `fixtures/`. A schema
change requires a new schema checksum and synchronized conformance tests.
Backward-incompatible changes require a new protocol version.
