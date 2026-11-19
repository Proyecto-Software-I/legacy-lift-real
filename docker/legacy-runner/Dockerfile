# Controlled runner for the cobol-to-java Migration Pack (development, docker-local).
# Only the operator builds this image; uploaded repositories never choose images or commands.
# Build: docker build -t legacylift/legacy-runner:1 docker/legacy-runner
FROM debian:bookworm-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends gnucobol3 openjdk-17-jdk-headless \
 && rm -rf /var/lib/apt/lists/* \
 && cobc --version | head -1 && javac -version
