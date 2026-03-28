(function () {
    const form = document.getElementById("upload-form");
    const fileInput = document.getElementById("source-file");
    const formatInput = document.getElementById("output-format");
    const startButton = document.getElementById("start-button");
    const downloadButton = document.getElementById("download-button");
    const resetButton = document.getElementById("reset-button");
    const badge = document.getElementById("status-badge");
    const jobIdEl = document.getElementById("job-id");
    const jobFormatEl = document.getElementById("job-format");
    const jobFramesEl = document.getElementById("job-frames");
    const jobExpiryEl = document.getElementById("job-expiry");
    const progressFill = document.getElementById("progress-fill");
    const progressPercent = document.getElementById("progress-percent");
    const progressMessage = document.getElementById("progress-message");
    const errorBox = document.getElementById("error-box");

    const maxUploadBytes = Number(form.dataset.maxUploadBytes || "0");
    let currentJobId = null;
    let pollingHandle = null;

    function setBadge(label, className) {
        badge.textContent = label;
        badge.className = "status-badge " + className;
    }

    function showError(message) {
        errorBox.textContent = message;
        errorBox.classList.remove("hidden");
    }

    function clearError() {
        errorBox.textContent = "";
        errorBox.classList.add("hidden");
    }

    function humanSize(bytes) {
        if (!Number.isFinite(bytes) || bytes <= 0) {
            return "0 B";
        }
        const units = ["B", "KB", "MB", "GB", "TB"];
        let value = bytes;
        let unitIndex = 0;
        while (value >= 1024 && unitIndex < units.length - 1) {
            value /= 1024;
            unitIndex += 1;
        }
        return value.toFixed(1) + " " + units[unitIndex];
    }

    function formatExpiry(unixSeconds) {
        if (!unixSeconds) {
            return "Unknown";
        }
        return new Date(unixSeconds * 1000).toLocaleString();
    }

    function setBusy(isBusy) {
        startButton.disabled = isBusy;
        fileInput.disabled = isBusy;
        formatInput.disabled = isBusy;
    }

    function resetView() {
        currentJobId = null;
        if (pollingHandle) {
            clearInterval(pollingHandle);
            pollingHandle = null;
        }
        form.reset();
        progressFill.style.width = "0%";
        progressPercent.textContent = "0%";
        progressMessage.textContent = "Waiting for a new upload.";
        jobIdEl.textContent = "Not started";
        jobFormatEl.textContent = "Adobe DNG";
        jobFramesEl.textContent = "Unknown";
        jobExpiryEl.textContent = "Not started";
        downloadButton.disabled = true;
        clearError();
        setBusy(false);
        setBadge("Idle", "idle");
    }

    function applyJobState(job) {
        jobIdEl.textContent = job.job_id;
        jobFormatEl.textContent = job.output_format_label;
        jobFramesEl.textContent = job.frame_count || "Unknown";
        jobExpiryEl.textContent = formatExpiry(job.expires_at);
        progressFill.style.width = String(job.progress_pct || 0) + "%";
        progressPercent.textContent = String(job.progress_pct || 0) + "%";
        progressMessage.textContent = job.progress_message || "Working...";

        if (job.status === "completed") {
            setBadge("Ready", "done");
            setBusy(false);
            downloadButton.disabled = !job.download_ready;
            return;
        }
        if (job.status === "failed") {
            setBadge("Failed", "error");
            setBusy(false);
            downloadButton.disabled = true;
            showError(job.error_message || "This job failed.");
            if (pollingHandle) {
                clearInterval(pollingHandle);
                pollingHandle = null;
            }
            return;
        }

        setBadge("Running", "running");
        downloadButton.disabled = true;
    }

    async function apiJson(url, options) {
        const response = await fetch(url, {
            headers: { "Content-Type": "application/json" },
            ...options,
        });
        if (!response.ok) {
            const payload = await safeJson(response);
            throw new Error(payload.detail || payload.message || "Request failed.");
        }
        return response.json();
    }

    async function safeJson(response) {
        try {
            return await response.json();
        } catch (_error) {
            return {};
        }
    }

    async function uploadSource(uploadConfig, file) {
        const headers = new Headers(uploadConfig.headers || {});
        if (!headers.has("Content-Type")) {
            headers.set("Content-Type", "application/octet-stream");
        }

        const response = await fetch(uploadConfig.url, {
            method: uploadConfig.method || "PUT",
            headers,
            body: file,
        });
        if (!response.ok) {
            const payload = await safeJson(response);
            throw new Error(payload.detail || "Upload failed.");
        }
    }

    function startPolling(jobId) {
        if (pollingHandle) {
            clearInterval(pollingHandle);
        }
        pollingHandle = setInterval(async function () {
            try {
                const job = await apiJson("/api/jobs/" + encodeURIComponent(jobId));
                applyJobState(job);
                if (job.status === "completed" || job.status === "failed") {
                    clearInterval(pollingHandle);
                    pollingHandle = null;
                }
            } catch (error) {
                clearInterval(pollingHandle);
                pollingHandle = null;
                setBusy(false);
                showError(error.message);
            }
        }, 2500);
    }

    form.addEventListener("submit", async function (event) {
        event.preventDefault();
        clearError();

        const file = fileInput.files && fileInput.files[0];
        if (!file) {
            showError("Pick a Canon .CR3 file first.");
            return;
        }
        if (!file.name.toLowerCase().endsWith(".cr3")) {
            showError("Only .CR3 files are accepted.");
            return;
        }
        if (maxUploadBytes > 0 && file.size > maxUploadBytes) {
            showError("This file is larger than the " + humanSize(maxUploadBytes) + " upload limit.");
            return;
        }

        try {
            setBusy(true);
            setBadge("Preparing", "running");
            progressMessage.textContent = "Creating upload job...";
            progressPercent.textContent = "0%";
            progressFill.style.width = "0%";
            jobFormatEl.textContent = formatInput.value === "cr3" ? "Canon CR3" : "Adobe DNG";

            const initiated = await apiJson("/api/jobs/initiate", {
                method: "POST",
                body: JSON.stringify({
                    filename: file.name,
                    file_size: file.size,
                    output_format: formatInput.value,
                }),
            });

            currentJobId = initiated.job_id;
            jobIdEl.textContent = currentJobId;
            jobExpiryEl.textContent = formatExpiry(initiated.expires_at);
            progressMessage.textContent = "Uploading source file...";
            progressPercent.textContent = "5%";
            progressFill.style.width = "5%";

            await uploadSource(initiated.upload, file);
            progressMessage.textContent = "Upload complete. Queueing worker job...";
            progressPercent.textContent = "10%";
            progressFill.style.width = "10%";

            const job = await apiJson("/api/jobs/" + encodeURIComponent(currentJobId) + "/upload-complete", {
                method: "POST",
                body: "{}",
            });
            applyJobState(job);
            startPolling(currentJobId);
        } catch (error) {
            setBadge("Failed", "error");
            setBusy(false);
            showError(error.message);
        }
    });

    downloadButton.addEventListener("click", async function () {
        if (!currentJobId) {
            return;
        }
        try {
            const payload = await apiJson("/api/jobs/" + encodeURIComponent(currentJobId) + "/download-link", {
                method: "POST",
                body: "{}",
            });
            window.location.href = payload.download.url;
            jobExpiryEl.textContent = formatExpiry(payload.expires_at);
        } catch (error) {
            showError(error.message);
        }
    });

    resetButton.addEventListener("click", function () {
        resetView();
    });

    resetView();
})();
