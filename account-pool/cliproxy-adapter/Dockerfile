ARG CPA_BUILDER
ARG CPA_RUNTIME
FROM ${CPA_BUILDER} AS builder
WORKDIR /app
COPY go.mod go.sum ./
RUN GOTOOLCHAIN=local go mod download
COPY . .
ARG VERSION
ARG COMMIT
RUN GOTOOLCHAIN=local CGO_ENABLED=1 GOOS=linux go build -mod=readonly -buildvcs=false -trimpath -ldflags="-s -w -X main.Version=${VERSION} -X main.Commit=${COMMIT} -X main.BuildDate=unknown" -o /CLIProxyAPI ./cmd/server/

FROM ${CPA_RUNTIME}
COPY --from=builder /etc/ssl/certs /etc/ssl/certs
COPY --from=builder /usr/share/zoneinfo /usr/share/zoneinfo
COPY --from=builder /CLIProxyAPI /CLIProxyAPI/CLIProxyAPI
COPY config.example.yaml /CLIProxyAPI/config.example.yaml
WORKDIR /CLIProxyAPI
EXPOSE 8317
ENV TZ=Asia/Shanghai
RUN cp /usr/share/zoneinfo/${TZ} /etc/localtime && echo "${TZ}" > /etc/timezone
CMD ["./CLIProxyAPI"]
