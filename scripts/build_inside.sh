#!/bin/sh
set -eu
cp -a /upstream /src
cd /src
git apply /out/scripts/patches/0000-security-baseline.patch
printf '%s\n' 'f547c08375f72e483c269c214f1a5190a8d81237687455549f829aac5a8d7d51  go.mod' '5c713561f5dce0e9c5c38977c925f54c94a4bec1095642cb6d04baaa612beacd  go.sum' | sha256sum -c -
export GOFLAGS=-mod=readonly GOTOOLCHAIN=local GOMAXPROCS=2
test "$(go version | cut -d ' ' -f 3)" = go1.27.3
git apply /out/scripts/patches/0000-approved-prebuilt-profiles.patch
git apply /out/scripts/patches/0001-flowsteward-neo4j-safety.patch
git apply /out/scripts/patches/0000-neo4j-latest-driver.patch
printf '%s\n' '2323795f12a07bdb1edf46b2e92083026d282547f4d5bb49aee55e5dbebf0a1d  go.mod' '119cf57e2df0d3d7aa5ba6b8bc7ed27db84d6aa6a502b10bf48553de447c1d4d  go.sum' | sha256sum -c -
go mod download github.com/neo4j/neo4j-go-driver/v6
cp -a /go/pkg/mod/github.com/neo4j/neo4j-go-driver/v6@v6.4.0 /driver
chmod -R u+w /driver
(cd /driver && git apply /out/scripts/patches/0002-neo4j-driver-socket-dialer.patch && go test -p 1 ./neo4j/internal/connector && go test -p 1 ./neo4j/internal/bolt -run 'TestFlowSteward|TestDechunkerWithTimeout' -count=1)
go mod edit -replace github.com/neo4j/neo4j-go-driver/v6=/driver
cat > cmd/internal/imports.go <<'IMPORTS'
package internal
import (
	_ "github.com/googleapis/mcp-toolbox/internal/sources/neo4j"
	_ "github.com/googleapis/mcp-toolbox/internal/tools/neo4j/neo4jcypher"
)
IMPORTS
# Literal registrations: no prompts/resources or unapproved database tools.
test "$(grep -c 'internal/' cmd/internal/imports.go)" = 2
sha256sum go.mod go.sum > /tmp/fs-module-lock
go test -p 1 ./internal/sources/neo4j ./internal/tools/neo4j/neo4jcypher ./internal/tools/neo4j/neo4jexecutecypher/classifier -count=1
export SOURCE_DATE_EPOCH=1793908560
for target in linux-amd64 linux-arm64; do
 arch=${target#linux-}
 mkdir -p /out/bin/$target
 CGO_ENABLED=0 GOOS=linux GOARCH=$arch go build -p 1 -trimpath -buildvcs=false -ldflags "-s -w -buildid= -X github.com/googleapis/mcp-toolbox/cmd.buildType=flowsteward.neo4j -X github.com/googleapis/mcp-toolbox/cmd.commitSha=06ea49d9a487a72c22419b5a8d5c22c11f0d7485" -o /out/bin/$target/toolbox .
 chmod 0755 /out/bin/$target/toolbox
 echo "[PASS] $target $(stat -c %s /out/bin/$target/toolbox) bytes"
done
sha256sum -c /tmp/fs-module-lock
