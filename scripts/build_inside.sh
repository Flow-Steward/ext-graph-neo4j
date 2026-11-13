#!/bin/sh
set -eu
sh /out/scripts/install-go-toolchain.sh /out/scripts/go-toolchain-selected.json
cp -a /upstream /src
cd /src
git apply /out/scripts/patches/0000-security-baseline.patch
printf '%s\n' 'fde9d602877fec959ba8204a9c1a60aa52a5d68904fb826bb0d84781a83ea836  go.mod' '9eb7ce830f6fb0fa62626ea4aee6dd7e663b6496e5f08b88bfef60930202ee18  go.sum' | sha256sum -c -
export GOFLAGS=-mod=readonly GOTOOLCHAIN=local GOMAXPROCS=2
test "$(go version | cut -d ' ' -f 3)" = go1.27.4
git apply /out/scripts/patches/0000-approved-prebuilt-profiles.patch
git apply /out/scripts/patches/0001-flowsteward-neo4j-safety.patch
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
 CGO_ENABLED=0 GOOS=linux GOARCH=$arch go build -p 1 -trimpath -buildvcs=false -ldflags "-s -w -buildid= -X github.com/googleapis/mcp-toolbox/cmd.buildType=flowsteward.neo4j -X github.com/googleapis/mcp-toolbox/cmd.commitSha=45ed205cc8682ed066c82e0b65bcd0e8fc3becce" -o /out/bin/$target/toolbox .
 chmod 0755 /out/bin/$target/toolbox
 echo "[PASS] $target $(stat -c %s /out/bin/$target/toolbox) bytes"
done
sha256sum -c /tmp/fs-module-lock
