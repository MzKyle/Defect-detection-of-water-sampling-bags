FROM debian:bookworm-slim AS build

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates cmake g++ make \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
COPY . .

RUN cmake -S cpp_backend -B build/cpp_backend \
    && cmake --build build/cpp_backend -j

FROM debian:bookworm-slim

WORKDIR /app
COPY --from=build /src/build/cpp_backend/waterbag_cpp_service /app/waterbag_cpp_service
COPY --from=build /src/config/cpp_backend/demo.ini /app/config/cpp_backend/demo.ini
COPY --from=build /src/demo_data /app/demo_data
COPY --from=build /src/artifacts/.gitkeep /app/artifacts/.gitkeep

CMD ["/app/waterbag_cpp_service", "--config", "config/cpp_backend/demo.ini", "--once"]
