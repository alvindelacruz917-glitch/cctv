
"use strict";

(() => {
    // Camera performance settings
    const FRAME_POLL_MS = 550;
    const STATUS_POLL_MS = 3000;
    const REQUEST_TIMEOUT_MS = 5000;

    const cameraImage = document.getElementById("camera-image");
    const placeholder = document.getElementById("image-placeholder");
    const imageMessage = document.getElementById("image-message");
    const refreshButton = document.getElementById("refresh-now");

    let currentObjectUrl = null;
    let lastFrameSignature = "";
    let frameRequestRunning = false;
    let stopped = false;
    let frameTimer = null;
    let statusTimer = null;

    function setMessage(message) {
        if (imageMessage) {
            imageMessage.textContent = message;
        }
    }

    function showPlaceholder(message) {
        if (cameraImage) {
            cameraImage.hidden = true;
        }

        if (placeholder) {
            placeholder.hidden = false;
        }

        setMessage(message);
    }

    function showCamera() {
        if (cameraImage) {
            cameraImage.hidden = false;
        }

        if (placeholder) {
            placeholder.hidden = true;
        }

        setMessage("Live camera connected");
    }

    async function fetchWithTimeout(url, options = {}) {
        const controller = new AbortController();
        const timeoutId = setTimeout(
            () => controller.abort(),
            REQUEST_TIMEOUT_MS
        );

        try {
            return await fetch(url, {
                ...options,
                signal: controller.signal,
                credentials: "same-origin",
                cache: "no-store"
            });
        } finally {
            clearTimeout(timeoutId);
        }
    }

    async function updateFrame() {
        if (stopped || frameRequestRunning || !cameraImage) {
            return;
        }

        frameRequestRunning = true;

        try {
            const response = await fetchWithTimeout(
                "/api/frame/latest",
                {
                    method: "GET",
                    headers: {
                        "Accept": "image/jpeg"
                    }
                }
            );

            if (response.status === 401 || response.status === 403) {
                showPlaceholder("Login required. Please sign in again.");
                return;
            }

            if (!response.ok) {
                throw new Error(
                    `Frame request failed: HTTP ${response.status}`
                );
            }

            const contentType =
                response.headers.get("content-type") || "";

            if (!contentType.toLowerCase().includes("image/jpeg")) {
                throw new Error(
                    `Expected JPEG, received: ${contentType || "unknown"}`
                );
            }

            // Use the server's frame identifier if provided.
            // Otherwise use the ETag, if available.
            const frameSignature =
                response.headers.get("X-Frame-ID") ||
                response.headers.get("ETag") ||
                "";

            // Only skip the download if the server provides a reliable
            // frame identifier and it has not changed.
            if (
                frameSignature &&
                frameSignature === lastFrameSignature
            ) {
                return;
            }

            const blob = await response.blob();

            if (blob.size === 0) {
                throw new Error("Received an empty image");
            }

            const newObjectUrl = URL.createObjectURL(blob);

            // Decode the new image before replacing the current image.
            const testImage = new Image();

            testImage.onload = () => {
                if (stopped) {
                    URL.revokeObjectURL(newObjectUrl);
                    return;
                }

                const previousUrl = currentObjectUrl;

                cameraImage.onload = () => {
                    if (previousUrl) {
                        URL.revokeObjectURL(previousUrl);
                    }
                };

                cameraImage.onerror = () => {
                    if (previousUrl) {
                        URL.revokeObjectURL(previousUrl);
                    }

                    URL.revokeObjectURL(newObjectUrl);
                    currentObjectUrl = null;
                    showPlaceholder("Could not display camera image.");
                };

                currentObjectUrl = newObjectUrl;
                cameraImage.src = newObjectUrl;

                if (frameSignature) {
                    lastFrameSignature = frameSignature;
                }

                showCamera();
            };

            testImage.onerror = () => {
                URL.revokeObjectURL(newObjectUrl);
                showPlaceholder("Invalid JPEG frame received.");
            };

            testImage.src = newObjectUrl;

        } catch (error) {
            if (error.name === "AbortError") {
                setMessage("Camera request timed out. Retrying...");
            } else {
                console.error("Camera frame error:", error);
                setMessage("Camera connection issue. Retrying...");
            }
        } finally {
            frameRequestRunning = false;

            // Schedule the next request only after the current one ends.
            if (!stopped) {
                frameTimer = setTimeout(updateFrame, FRAME_POLL_MS);
            }
        }
    }

    async function updateStatus() {
        if (stopped) return;

        try {
            const response = await fetchWithTimeout("/api/status");

            if (response.status === 401 || response.status === 403) {
                console.warn("Dashboard authentication required.");
                return;
            }

            if (!response.ok) {
                throw new Error(`Status HTTP ${response.status}`);
            }

            const data = await response.json();

            // Update these selectors only if they exist in your HTML.
            const statusElement =
                document.getElementById("camera-status");

            if (statusElement) {
                const connected =
                    data.camera_connected === true ||
                    data.camera_status === "connected" ||
                    data.connected === true;

                statusElement.textContent =
                    connected ? "Connected" : "Disconnected";

                statusElement.dataset.status =
                    connected ? "connected" : "disconnected";
            }

            // Preserve the ability to inspect the status response.
            document.dispatchEvent(
                new CustomEvent("camera-status-updated", {
                    detail: data
                })
            );

        } catch (error) {
            console.warn("Status update failed:", error.message);
        } finally {
            if (!stopped) {
                statusTimer = setTimeout(
                    updateStatus,
                    STATUS_POLL_MS
                );
            }
        }
    }

    if (refreshButton) {
        refreshButton.addEventListener("click", () => {
            if (frameTimer) {
                clearTimeout(frameTimer);
            }

            updateFrame();
        });
    }

    function cleanup() {
        stopped = true;

        if (frameTimer) clearTimeout(frameTimer);
        if (statusTimer) clearTimeout(statusTimer);

        if (currentObjectUrl) {
            URL.revokeObjectURL(currentObjectUrl);
            currentObjectUrl = null;
        }
    }

    window.addEventListener("pagehide", cleanup);

    // Start camera and status polling.
    showPlaceholder("Connecting to camera...");
    updateFrame();
    updateStatus();
})();
