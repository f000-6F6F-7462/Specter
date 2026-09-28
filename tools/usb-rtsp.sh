#!/usr/bin/env bash
# Publishes a USB or built-in camera as an RTSP stream that Specter can pull. Works on macOS and
# Linux.
#
# The chain, and why it has this shape:
#
#   camera -> ffmpeg -> mediamtx on this host -> go2rtc (Docker) -> Specter's camera process
#
# Specter's camera process never opens the camera itself. The camera manager registers the
# camera's source_url in go2rtc, and the camera process reads the stream back from go2rtc at
# rtsp://127.0.0.1:8554/<camera-id>. go2rtc runs in a container and cannot reach a local USB
# device, so something on the host has to serve RTSP to it: that is mediamtx, fed by ffmpeg.
#
# The port is 18554 and not 8554, because the go2rtc container already publishes 8554.
#
# Usage:
#   tools/usb-rtsp.sh "Camera name"         # macOS: BY NAME, which is what you want (see below)
#   tools/usb-rtsp.sh                    # first camera (device 0 / /dev/video0)
#   tools/usb-rtsp.sh /dev/video2        # Linux: a specific device
#   SIZE=1920x1080 FPS=30 tools/usb-rtsp.sh "Camera name"
#   INPUT_FORMAT=mjpeg tools/usb-rtsp.sh # Linux: many USB cameras need MJPEG above 640x480
#   tools/usb-rtsp.sh "Camera name" --modes # print the device's supported modes and exit
#
# On macOS, prefer the device NAME over its index. avfoundation renumbers the devices between
# runs, so the USB camera that was [1] can be [0] the next time and the script would silently
# capture the built-in camera instead. Names are stable; avfoundation accepts them in place of
# the index.
#
# List the capture devices:
#   macOS: ffmpeg -f avfoundation -list_devices true -i ""
#   Linux: v4l2-ctl --list-devices     (or: ls /dev/video*)
#
# Ctrl+C stops mediamtx and the ffmpeg it started.

set -euo pipefail

SIZE="${SIZE:-1280x720}"
FPS="${FPS:-30}"
PORT="${PORT:-18554}"
STREAM="${STREAM:-usb}"
INPUT_FORMAT="${INPUT_FORMAT:-}"

operating_system="$(uname -s)"

# Each platform has its own capture backend, and its own default for "the first camera".
case "$operating_system" in
Darwin)
	DEVICE="${1:-${DEVICE:-0}}"
	# avfoundation takes "<video>:<audio>"; "none" keeps the microphone out of the stream.
	capture_input="-f avfoundation -framerate ${FPS} -video_size ${SIZE} -i \"${DEVICE}:none\""
	device_hint='ffmpeg -f avfoundation -list_devices true -i ""'
	;;
Linux)
	DEVICE="${1:-${DEVICE:-/dev/video0}}"
	input_format_option=""
	[[ -n "$INPUT_FORMAT" ]] && input_format_option="-input_format ${INPUT_FORMAT} "
	capture_input="-f v4l2 ${input_format_option}-framerate ${FPS} -video_size ${SIZE} -i ${DEVICE}"
	device_hint='v4l2-ctl --list-devices'
	;;
*)
	echo "unsupported operating system: ${operating_system}" >&2
	exit 1
	;;
esac

for tool in ffmpeg mediamtx; do
	command -v "$tool" >/dev/null 2>&1 || {
		echo "${tool} is not installed." >&2
		case "$operating_system" in
		Darwin) echo "  brew install ffmpeg mediamtx" >&2 ;;
		Linux) echo "  ffmpeg: apt install ffmpeg | dnf install ffmpeg" >&2
			echo "  mediamtx: https://github.com/bluenviron/mediamtx/releases (single binary)" >&2 ;;
		esac
		exit 1
	}
done

if [[ "$operating_system" == "Linux" && ! -e "$DEVICE" ]]; then
	echo "${DEVICE} does not exist. Available: $(echo /dev/video* 2>/dev/null)" >&2
	echo "List them with: ${device_hint}" >&2
	exit 1
fi

# `--modes` answers "which SIZE and FPS may I ask for?", the question behind most capture failures.
if [[ "${2:-}" == "--modes" || "${MODES:-}" == "1" ]]; then
	case "$operating_system" in
	Darwin)
		# avfoundation has no list command, but it prints every mode when refusing an impossible
		# size. Collect the distinct resolutions with the highest frame rate each offers.
		probe_output="$(ffmpeg -hide_banner -f avfoundation -video_size 1x1 \
			-i "${DEVICE}:none" -t 0.1 -f null - 2>&1 || true)"
		modes="$(printf '%s\n' "$probe_output" |
			sed -n 's/.*[^0-9]\([0-9]\{2,\}x[0-9]\{2,\}\)@\[\([0-9.]*\).*/\1 \2/p' |
			sort -t' ' -k1,1 -k2,2gr |
			awk '!seen[$1]++ {printf "%-12s up to %.0f fps\n", $1, $2}' |
			sort -t'x' -k1,1n)"
		if [[ -z "$modes" ]]; then
			# No mode list means the device could not be opened at all, which is a different
			# problem from asking for the wrong size, so show what ffmpeg actually said.
			echo "cannot read the modes of device ${DEVICE}:" >&2
			printf '%s\n' "$probe_output" | grep -iE 'cannot|error|denied' | head -3 >&2
			echo >&2
			echo "'Cannot use <name>' means another application holds the camera. Close" >&2
			echo "QuickTime Player, FaceTime, Photo Booth or a browser tab using it, then retry." >&2
			exit 1
		fi
		printf '%s\n' "$modes"
		;;
	Linux) v4l2-ctl --device="$DEVICE" --list-formats-ext ;;
	esac
	exit 0
fi

# How a container reaches this host differs by platform, and getting it wrong is the most common
# reason go2rtc reports that it cannot connect.
docker_host_address() {
	# Docker Desktop (macOS, and Docker Desktop on Linux) provides this name.
	if [[ "$operating_system" == "Darwin" ]]; then
		printf 'host.docker.internal'
		return
	fi
	# Native Docker on Linux does not: a container reaches the host at its network's gateway.
	local gateway
	for network in specter_default bridge; do
		gateway="$(docker network inspect "$network" \
			--format '{{range .IPAM.Config}}{{.Gateway}}{{end}}' 2>/dev/null || true)"
		if [[ -n "$gateway" ]]; then
			printf '%s' "$gateway"
			return
		fi
	done
	# Nothing could be detected; this name works only if go2rtc was given
	# extra_hosts: ["host.docker.internal:host-gateway"].
	printf 'host.docker.internal'
}

host_address="$(docker_host_address)"

# mediamtx starts ffmpeg itself and restarts it if it exits, so a camera that is unplugged and
# plugged back in recovers without restarting this script. Only RTSP is enabled; the other
# protocols would just take ports for nothing.
#
# Two encoder choices are about how quickly a viewer gets a picture:
#   -force_key_frames every second, rather than a GOP counted in frames, because a camera often
#     delivers fewer frames per second than asked for. A GOP of FPS*2 frames would then stretch
#     to several seconds, and go2rtc and Specter both need a keyframe before they decode anything.
#   -pkt_size 1316 keeps the RTP packets under the limit mediamtx enforces, so it does not have
#     to remux every packet into smaller ones and warn about it.
# The trap covers the signals too, not only EXIT, or Ctrl+C leaves the directory behind.
work_directory="$(mktemp -d "${TMPDIR:-/tmp}/usb-rtsp.XXXXXX")"
trap 'rm -rf "$work_directory"' EXIT INT TERM
config_file="${work_directory}/mediamtx.yml"

# Everything except RTSP over TCP is turned off, and each "no" earns its place:
#   moq       - Media over QUIC generates a self-signed auto.crt/auto.key in the working
#               directory on startup. Off, nothing is generated and no certificate is needed.
#   rtspTransports - left at its default, RTSP also binds UDP 8000 and 8001. Specter's API
#               owns TCP 8000, so those listeners are confusing to find even though UDP does
#               not collide with it. ffmpeg publishes over TCP here anyway.
# The rest simply keep mediamtx from taking ports nothing in this setup uses.
cat >"$config_file" <<YAML
logLevel: info
rtspAddress: :${PORT}
rtspTransports: [tcp]
rtmp: no
hls: no
webrtc: no
srt: no
moq: no
paths:
  ${STREAM}:
    runOnInit: 'ffmpeg -hide_banner -loglevel warning ${capture_input} -an -c:v libx264 -preset veryfast -tune zerolatency -profile:v baseline -pix_fmt yuv420p -force_key_frames "expr:gte(t,n_forced*1)" -f rtsp -rtsp_transport tcp -pkt_size 1316 rtsp://127.0.0.1:${PORT}/${STREAM}'
    runOnInitRestart: yes
YAML

cat <<INFO
platform       : ${operating_system}
capture device : ${DEVICE}  (${SIZE} @ ${FPS}fps)

  check the stream from this host:
    ffprobe -rtsp_transport tcp rtsp://127.0.0.1:${PORT}/${STREAM}

  give this to Specter as the camera's source_url:
    rtsp://${host_address}:${PORT}/${STREAM}

  list capture devices:
    ${device_hint}

INFO

case "$operating_system" in
Darwin)
	echo "The first run asks for camera access; macOS grants it to the terminal application, so"
	echo "approve the prompt and run this again if ffmpeg cannot open the device."
	;;
Linux)
	echo "If ffmpeg reports a permission error, add your user to the 'video' group and log in again."
	echo "If go2rtc cannot connect, allow the Docker bridge to reach port ${PORT} in the firewall."
	;;
esac
echo

# Not exec'd: the shell has to survive mediamtx so the EXIT trap can remove the working directory.
# Ctrl+C reaches mediamtx anyway, because it goes to the whole process group.
cd "$work_directory"
mediamtx "$config_file"
