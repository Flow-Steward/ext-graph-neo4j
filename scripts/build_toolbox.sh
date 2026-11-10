#!/bin/sh
# Reproducible Neo4j-only Toolbox build with bounded Bolt transport and verified TLS.
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
TOOLBOX_TAG=v1.3.0
TOOLBOX_COMMIT=b0010060a939455d91e4f8ba62dc3e72b1892469
GO_IMAGE=golang:1.26.10-bookworm@sha256:5de7c99eb78ca5e515a4c340235d0955ec5964a5071c492c15503d7c267da473
PATCH=scripts/patches/0001-flowsteward-neo4j-safety.patch

docker run --rm --cpus 1 --memory 2g --label flow-steward.audit=neo4j-20261101 \
  -e TOOLBOX_TAG="$TOOLBOX_TAG" -e TOOLBOX_COMMIT="$TOOLBOX_COMMIT" \
  -e GO_IMAGE="$GO_IMAGE" -e PATCH="$PATCH" -e PATCH_CONTENT="$(cat "$ROOT/$PATCH")" \
  -e DRIVER_PATCH_CONTENT="$(cat "$ROOT/scripts/patches/0002-neo4j-driver-socket-dialer.patch")" \
  -v flow-steward-db-batch-go-cache:/go/pkg/mod -v flow-steward-db-batch-build-cache:/root/.cache/go-build \
  -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" \
  -v "$ROOT:/out" "$GO_IMAGE" sh -c '
    set -eu
    git clone --quiet --filter=blob:none --branch "$TOOLBOX_TAG" --depth 1 \
      https://github.com/googleapis/mcp-toolbox.git /src
    test "$(git -C /src rev-parse HEAD)" = "$TOOLBOX_COMMIT"
    cd /src
    git apply /out/scripts/patches/0000-security-baseline.patch
    printf "%s\n" "7f367f79f68cc28cfcb8c7d36c5533ef79f5016aaeba4443a795a46132105fb5  go.mod" "ca2ace78373b79e7c2b048e69ef22abfb93f1b3a537b7f035ad7c1a0c28542e9  go.sum" | sha256sum -c -
    export GOFLAGS=-mod=readonly GOTOOLCHAIN=local
    printf "%s\n" "$PATCH_CONTENT" > /tmp/neo4j.patch
    git apply /tmp/neo4j.patch
    go mod download github.com/neo4j/neo4j-go-driver/v6
    cp -R /go/pkg/mod/github.com/neo4j/neo4j-go-driver/v6@v6.0.0 /driver
    chmod -R u+w /driver
    printf "%s\n" "$DRIVER_PATCH_CONTENT" > /tmp/driver.patch
    cd /driver
    git apply /tmp/driver.patch
    gofmt -w neo4j/config/driver.go neo4j/internal/connector/connector.go neo4j/internal/bolt/dechunker.go
    go test -p 1 ./neo4j/internal/connector
    go test -p 1 ./neo4j/internal/bolt -run "TestFlowSteward|TestDechunkerWithTimeout"
    cd /src
    go mod edit -replace github.com/neo4j/neo4j-go-driver/v6=/driver
    sha256sum go.mod go.sum > /tmp/fs-security-module-lock
    awk '\''/internal\/(sources|tools)\// {
      if ($2 ~ /github.com\/googleapis\/mcp-toolbox\/internal\/(sources\/neo4j|tools\/neo4j\/neo4jcypher)\"$/) print;
      next
    } { print }'\'' cmd/internal/imports.go > /tmp/imports.go
    test "$(grep -c "internal/\(sources\|tools\)/" /tmp/imports.go)" = 2
    mv /tmp/imports.go cmd/internal/imports.go
    gofmt -w internal/sources/neo4j/*.go internal/tools/neo4j/*/*.go
    go test -p 1 ./internal/sources/neo4j ./internal/tools/neo4j/neo4jcypher ./internal/tools/neo4j/neo4jexecutecypher/classifier
    export SOURCE_DATE_EPOCH=1779400183
    for target in linux-amd64 linux-arm64; do
      arch=${target#linux-}
      mkdir -p "/tmp/neo4j-bin/$target"
      CGO_ENABLED=0 GOOS=linux GOARCH="$arch" go build -p 1 -trimpath -buildvcs=false \
        -ldflags "-s -w -buildid= -X github.com/googleapis/mcp-toolbox/cmd.buildType=flowsteward.neo4j -X github.com/googleapis/mcp-toolbox/cmd.commitSha=$TOOLBOX_COMMIT" \
        -o "/tmp/neo4j-bin/$target/toolbox" .
      chmod 0755 "/tmp/neo4j-bin/$target/toolbox"
      echo "[PASS] $target: $(stat -c %s "/tmp/neo4j-bin/$target/toolbox") bytes"
    done
    sha256sum -c /tmp/fs-security-module-lock
    {
      printf "{\n  \"tag\": \"%s\",\n  \"commit\": \"%s\",\n  \"go_image\": \"%s\",\n" "$TOOLBOX_TAG" "$TOOLBOX_COMMIT" "$GO_IMAGE"
      printf "  \"source_date_epoch\": 1779400183,\n  \"patch\": \"%s\",\n  \"patch_sha256\": \"%s\",\n" "$PATCH" "$(sha256sum /tmp/neo4j.patch | cut -d " " -f 1)"
      printf "  \"driver\": {\"module\": \"github.com/neo4j/neo4j-go-driver/v6\", \"version\": \"v6.0.0\", \"module_sum\": \"h1:xVAi6YLOfzXUx+1Lc/F2dUhpbN76BfKleZbAlnDFRiA=\", \"patch\": \"scripts/patches/0002-neo4j-driver-socket-dialer.patch\", \"patch_sha256\": \"%s\"},\n" "$(sha256sum /tmp/driver.patch | cut -d " " -f 1)"
      printf "  \"packages\": [\"github.com/googleapis/mcp-toolbox/internal/sources/neo4j\", \"github.com/googleapis/mcp-toolbox/internal/tools/neo4j/neo4jcypher\"],\n  \"binaries\": {\n"
      comma=""
      for target in linux-amd64 linux-arm64; do
        printf "%s    \"%s\": {\"sha256\": \"%s\", \"size\": %s}" "$comma" "$target" "$(sha256sum "/tmp/neo4j-bin/$target/toolbox" | cut -d " " -f 1)" "$(stat -c %s "/tmp/neo4j-bin/$target/toolbox")"
        comma=",
"
      done
      printf "\n  }\n}\n"
    } > /tmp/neo4j-provenance.json
    # Publish only complete builds; rename each file on its destination filesystem.
    for target in linux-amd64 linux-arm64; do
      mkdir -p "/out/bin/$target"
      pending=$(mktemp "/out/bin/$target/.toolbox.XXXXXX")
      cp "/tmp/neo4j-bin/$target/toolbox" "$pending"
      chmod 0755 "$pending"
      chown "$HOST_UID:$HOST_GID" "$pending"
      mv -f "$pending" "/out/bin/$target/toolbox"
    done
    pending=$(mktemp /out/.toolbox-provenance.XXXXXX)
    cp /tmp/neo4j-provenance.json "$pending"
    chmod 0644 "$pending"
    chown "$HOST_UID:$HOST_GID" "$pending"
    mv -f "$pending" /out/toolbox-provenance.json
  '

python3 "$ROOT/scripts/native_build_info.py"
