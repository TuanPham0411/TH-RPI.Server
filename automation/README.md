# TH-Server automation runtime

The native `th-automation.service` owns timer ticks and Function isolation for TH AI.Agent Studio. The storage broker continues to run without a Docker socket. Only this small host supervisor can launch Functions, through a bearer-authenticated Unix socket in the existing private runtime directory.

Each Function uses the already deployed immutable AI API image, UID 65534, no network, read-only root, no capabilities, no extra privileges, 16 PIDs, 64 MiB memory, 0.5 CPU and a 12-second wall deadline. Only a temporary JSON input file is mounted read-only. No application environment, credential, host home or Docker socket is passed to the container. Output is bounded and the container is forcibly removed on completion or timeout. Node's VM is only an additional language boundary; the container and supervisor limits provide the isolation boundary.

The supervisor calls the application's internal tick endpoint over host loopback every minute. It uses the existing AI storage identity from the private host environment. The application records each scheduled claim and run in one Drive-first transaction before execution. Interrupted runs are marked and never automatically replayed, because external writes may have happened. Public nginx blocks `/api/internal/`.

Install from an immutable release with `install.py <platform-sha>` through the trusted host helper. No database, volume or cloud backup is deleted. The service reads the current recorded application image instead of pulling images for each Function.

Tests execute hostile and ordinary functions in real restricted Docker containers on hosted CI, including a VM escape attempt, filesystem/socket/secret isolation, network isolation, output flooding and CPU/microtask loops.
