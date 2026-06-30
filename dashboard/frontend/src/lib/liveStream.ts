export function startWebRTC(
  video: HTMLVideoElement,
  camera: string,
  onError?: () => void,
  onSuccess?: () => void
): () => void {
  const pc = new RTCPeerConnection({
    iceServers: [{ urls: "stun:stun.l.google.com:19302" }],
  });

  pc.addTransceiver("video", { direction: "recvonly" });

  pc.ontrack = (ev) => {
    if (video.srcObject !== ev.streams[0]) {
      video.srcObject = ev.streams[0];
    }
  };

  pc.onconnectionstatechange = () => {
    if (
      pc.connectionState === "failed" ||
      pc.connectionState === "disconnected"
    ) {
      onError?.();
    } else if (pc.connectionState === "connected") {
      onSuccess?.();
    }
  };

  pc.createOffer()
    .then((offer) => pc.setLocalDescription(offer))
    .then(() => {
      const u = new URL("/api/webrtc", window.location.origin);
      u.searchParams.set("src", camera);
      return fetch(u.toString(), {
        method: "POST",
        headers: { "Content-Type": "application/sdp" },
        body: pc.localDescription?.sdp,
      });
    })
    .then((r) => r.text())
    .then((sdp) => {
      pc.setRemoteDescription(new RTCSessionDescription({ type: "answer", sdp }));
    })
    .catch((e) => {
      console.error("WebRTC Error:", e);
      onError?.();
    });

  return () => pc.close();
}
