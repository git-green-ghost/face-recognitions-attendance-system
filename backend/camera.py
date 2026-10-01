import cv2


def _open_capture(camera_index: int):
    """Open one camera using the most stable local backend available."""
    cap = cv2.VideoCapture(camera_index, cv2.CAP_DSHOW)
    if not cap.isOpened():
        cap.release()
        cap = cv2.VideoCapture(camera_index)
    if cap.isOpened():
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    return cap


def start_camera(camera_index: int = 0):
    """Open the webcam and return the capture object."""
    cap = _open_capture(camera_index)

    if not cap.isOpened():
        cap.release()
        raise RuntimeError("Unable to access the camera. Check webcam permissions.")

    return cap


def probe_camera(camera_index: int) -> dict[str, object]:
    """Check whether a camera index can open and return a frame."""
    cap = _open_capture(camera_index)
    opened = cap.isOpened()
    frame_ok = False
    width = 0
    height = 0
    if opened:
        frame_ok, frame = cap.read()
        if frame_ok and frame is not None:
            height, width = frame.shape[:2]
    cap.release()
    return {
        "camera_index": camera_index,
        "opened": bool(opened),
        "frame_ok": bool(frame_ok),
        "resolution": f"{width}x{height}" if frame_ok and width and height else "",
    }


def list_available_cameras(max_index: int = 4) -> list[dict[str, object]]:
    """Scan a small range of local camera indexes and report working devices."""
    results: list[dict[str, object]] = []
    consecutive_failures = 0
    for camera_index in range(max(1, max_index)):
        probe = probe_camera(camera_index)
        if bool(probe["opened"]):
            results.append(probe)
            consecutive_failures = 0
        else:
            consecutive_failures += 1
            if consecutive_failures >= 2 and camera_index >= 1:
                break
    return results
