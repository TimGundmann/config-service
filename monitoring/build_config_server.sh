#!/bin/sh
set -eu
revision=2320134bb36be85fd01c6f9988d5cb9b23cb9296
source_dir=/home/tim/knitty-log-rotation-20261031/config-source
git clone --branch master --single-branch https://github.com/TimGundmann/config-service.git "$source_dir"
cd "$source_dir"
test "$(git rev-parse HEAD)" = "$revision"
docker run --rm --memory 1024m --entrypoint sh \
    -v "$source_dir:/workspace" -w /workspace \
    config-server:70d543c128ba0fc8d8993dc809f146604a09d833 -ec '
    url=https://repo.maven.apache.org/maven2/org/apache/maven/apache-maven/3.9.9/apache-maven-3.9.9-bin.tar.gz
    wget -q "$url" -O /tmp/maven.tar.gz
    wget -q "$url.sha512" -O /tmp/maven.sha512
    test "$(sha512sum /tmp/maven.tar.gz | cut -d " " -f 1)" = "$(cat /tmp/maven.sha512)"
    tar -xzf /tmp/maven.tar.gz -C /tmp
    /tmp/apache-maven-3.9.9/bin/mvn -B -Dmaven.repo.local=/workspace/.m2/repository verify
    '
python3 -m unittest discover -s deploy/tests
docker build -t "config-server:$revision" .
python3 deploy/blue_green.py deploy --service config-server --image "config-server:$revision"
