# Changelog

## 1.0.17 - 2026-11-26

- Use the upstream Gorilla WebSocket masking fix for GHSA-w67g-5rqw-f597.
- Rebuild Linux AMD64 and ARM64 from the reviewed Go module lock.
- Update Google API to v0.317.0 and Google Cloud Auth to v0.24.5.

## 1.0.16 - 2026-11-24

- Update the pinned Toolbox source to v1.18.0 and refresh the reviewed Linux AMD64 and ARM64 dependency locks.
- Update Cloud SQL Connector, Spanner, LZ4 and the OpenTelemetry Auto SDK in the build graph.
- Update development PyYAML to 6.0.4.

## 1.0.15 - 2026-11-21

- Refresh the reviewed Toolbox Go dependency lock and rebuild both Linux runtime targets.

## 1.0.14 - 2026-11-20

- Package the reviewed Git revision and the verified runtime binary with its declared build provenance. Unexpected workspace files stop packaging.

## 1.0.13

- Rebuild Linux AMD64 and ARM64 native payloads with grpc-gateway v2.34.0 and its required indirect modules on the pinned Toolbox v1.17.0 and verified Go 1.27.4 baseline.
- Preserve the existing connector controls, approved profiles and dependency security fixes.

## 1.0.12 - 2026-11-15

### Added

- Added a Support tab with links for reporting a problem, asking a question or requesting a feature in this extension's GitHub repository, and what to include in a report.

## 1.0.11 - 2026-11-14

- Declare extension host contract 1.5.0 for the artifact-free service descriptor, so current hosts can install the release. Workflow action IDs, inputs, outputs and provider behavior are unchanged.

## 1.0.10

- Rebuild Linux AMD64 and ARM64 native payloads with google.golang.org/api v0.315.0 on the pinned Toolbox v1.17.0 and verified Go 1.27.4 baseline.
- Preserve the other selected dependency versions, connector controls and approved profiles; reject the superseded Google API module during package verification.


## 1.0.9

- Refresh Toolbox to v1.17.0 and the reviewed dependency lock; build both native targets in CI while preserving connector safety and profile restrictions.
