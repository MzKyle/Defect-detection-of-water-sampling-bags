FROM debian:bookworm-slim AS build

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates cmake g++ make python3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
COPY . .

RUN python3 scripts/generate_demo_data.py --output build/generated_demo

RUN cmake -S cpp_backend -B build/cpp_backend -DCMAKE_BUILD_TYPE=RelWithDebInfo \
    && cmake --build build/cpp_backend -j

FROM debian:bookworm-slim

WORKDIR /app
COPY --from=build /src/build/cpp_backend/waterbag_cpp_service /app/waterbag_cpp_service
COPY --from=build /src/config/cpp_backend/verify.ini /app/config/cpp_backend/verify.ini
COPY --from=build /src/build/generated_demo /app/build/generated_demo

RUN groupadd --system waterbag \
    && useradd --system --create-home --gid waterbag waterbag \
    && mkdir -p /app/build/verify/cpp_backend \
    && chown -R waterbag:waterbag /app

USER waterbag

CMD ["/app/waterbag_cpp_service", "--config", "config/cpp_backend/verify.ini", "--once"]
