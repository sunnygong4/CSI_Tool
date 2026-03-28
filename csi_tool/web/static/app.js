(function () {
    const ACTIVE_JOB_STATUSES = new Set(["initiated", "uploaded", "processing"]);
    const STORAGE_KEY = "csi-tool.current-job-id";
    const STALE_HINT_AFTER_POLLS = 8;

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
    const statusHint = document.getElementById("status-hint");
    const errorBox = document.getElementById("error-box");

    const maxUploadBytes = Number(form.dataset.maxUploadBytes || "0");
    let currentJobId = null;
    let pollingHandle = null;
    let lastProgressSignature = "";
    let stalePollCount = 0;

    function setBadge(label, className) {
        badge.textContent = label;
        badge.className = "status-badge " + className;
    }

    function setStatusHint(message) {
        statusHint.textContent = message || "";
    }

    function showError(message) {
        errorBox.textContent = message;
        errorBox.classList.remove("hidden");
    }

    function clearError() {
        errorBox.textContent = "";
        errorBox.classList.add("hidden");
    }

    function saveCurrentJobId(jobId) {
        try {
            if (jobId) {
                window.localStorage.setItem(STORAGE_KEY, jobId);
            }
        } catch (_error) {
            // Ignore storage failures; the page can still function without persistence.
        }
    }

    function loadCurrentJobId() {
        try {
            return window.localStorage.getItem(STORAGE_KEY);
        } catch (_error) {
            return null;
        }
    }

    function clearCurrentJobId() {
        try {
            window.localStorage.removeItem(STORAGE_KEY);
        } catch (_error) {
            // Ignore storage failures.
        }
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

    function stopPolling() {
        if (pollingHandle) {
            clearInterval(pollingHandle);
            pollingHandle = null;
        }
    }

    function resetView(options) {
        const clearStoredJob = !options || options.clearStoredJob !== false;
        currentJobId = null;
        stopPolling();
        if (clearStoredJob) {
            clearCurrentJobId();
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
        setStatusHint("");
        setBusy(false);
        setBadge("Idle", "idle");
        lastProgressSignature = "";
        stalePollCount = 0;
    }

    function updateStaleHint(job) {
        const signature = [job.status, job.progress_pct || 0, job.progress_message || ""].join("|");
        if (signature === lastProgressSignature) {
            stalePollCount += 1;
        } else {
            stalePollCount = 0;
            lastProgressSignature = signature;
        }

        if (ACTIVE_JOB_STATUSES.has(job.status) && stalePollCount >= STALE_HINT_AFTER_POLLS) {
            setStatusHint("Still working. Large DNG burst jobs can stay on one frame for a while.");
            return;
        }
        setStatusHint("");
    }

    function applyJobState(job) {
        currentJobId = job.job_id;
        saveCurrentJobId(job.job_id);
        jobIdEl.textContent = job.job_id;
        jobFormatEl.textContent = job.output_format_label;
        jobFramesEl.textContent = job.frame_count || "Unknown";
        jobExpiryEl.textContent = formatExpiry(job.expires_at);
        progressFill.style.width = String(job.progress_pct || 0) + "%";
        progressPercent.textContent = String(job.progress_pct || 0) + "%";
        progressMessage.textContent = job.progress_message || "Working...";
        updateStaleHint(job);

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
            stopPolling();
            return;
        }

        setBadge("Running", "running");
        downloadButton.disabled = true;
    }

    async function safeJson(response) {
        try {
            return await response.json();
        } catch (_error) {
            return {};
        }
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

    async function fetchAndApplyJob(jobId) {
        const job = await apiJson("/api/jobs/" + encodeURIComponent(jobId));
        clearError();
        applyJobState(job);
        if (ACTIVE_JOB_STATUSES.has(job.status)) {
            setBusy(true);
            startPolling(jobId);
        } else {
            stopPolling();
        }
        return job;
    }

    function startPolling(jobId) {
        stopPolling();
        pollingHandle = setInterval(async function () {
            try {
                const job = await apiJson("/api/jobs/" + encodeURIComponent(jobId));
                clearError();
                applyJobState(job);
                if (!ACTIVE_JOB_STATUSES.has(job.status)) {
                    stopPolling();
                }
            } catch (error) {
                stopPolling();
                setBusy(false);
                showError(error.message);
            }
        }, 2500);
    }

    async function recoverJobState() {
        const storedJobId = loadCurrentJobId();
        setBusy(true);
        setBadge("Recovering", "running");
        progressMessage.textContent = "Reconnecting to your last job...";

        if (storedJobId) {
            try {
                await fetchAndApplyJob(storedJobId);
                return;
            } catch (_error) {
                clearCurrentJobId();
            }
        }

        try {
            const job = await apiJson("/api/jobs/recover");
            clearError();
            applyJobState(job);
            if (ACTIVE_JOB_STATUSES.has(job.status)) {
                setBusy(true);
                startPolling(job.job_id);
            } else {
                setBusy(false);
            }
        } catch (_error) {
            resetView({ clearStoredJob: true });
        }
    }

    form.addEventListener("submit", async function (event) {
        event.preventDefault();
        clearError();
        setStatusHint("");
        lastProgressSignature = "";
        stalePollCount = 0;

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
            saveCurrentJobId(currentJobId);
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
            if (ACTIVE_JOB_STATUSES.has(job.status)) {
                startPolling(currentJobId);
            }
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
        resetView({ clearStoredJob: true });
    });

    resetView({ clearStoredJob: false });
    void recoverJobState();
})();
