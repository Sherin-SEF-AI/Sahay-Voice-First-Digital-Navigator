/**
 * SAHAY (सहाय) — Main Application Logic
 *
 * Manages WebSocket connections, microphone capture, audio playback,
 * GPA Action Log panel, and UI state for the SAHAY dashboard.
 */

(function () {
    "use strict";

    // ── State ──────────────────────────────────────────────
    var state = {
        sessionId: crypto.randomUUID(),
        voiceWs: null,
        screenWs: null,
        micActive: false,
        micAudioContext: null,
        playbackAudioContext: null,
        micStream: null,
        micProcessor: null,
        volume: 0.8,
        reconnectDelay: 3000,
        maxReconnectDelay: 30000,
        currentGpaTask: null,
        gpaSteps: {},
    };

    // ── DOM Elements ───────────────────────────────────────
    var dom = {
        connectionStatus: document.getElementById("connectionStatus"),
        statusText: document.querySelector(".status-text"),
        languageIndicator: document.getElementById("languageIndicator"),
        transcriptLog: document.getElementById("transcriptLog"),
        screenImage: document.getElementById("screenImage"),
        placeholderMessage: document.getElementById("placeholderMessage"),
        actionOverlay: document.getElementById("actionOverlay"),
        actionText: document.getElementById("actionText"),
        loadingSpinner: document.getElementById("loadingSpinner"),
        currentUrl: document.getElementById("currentUrl"),
        micButton: document.getElementById("micButton"),
        textInput: document.getElementById("textInput"),
        sendButton: document.getElementById("sendButton"),
        helpButton: document.getElementById("helpButton"),
        volumeSlider: document.getElementById("volumeSlider"),
        confirmationDialog: document.getElementById("confirmationDialog"),
        confirmationMessage: document.getElementById("confirmationMessage"),
        confirmYes: document.getElementById("confirmYes"),
        confirmNo: document.getElementById("confirmNo"),
        // GPA elements
        gpaPanel: document.getElementById("gpaPanel"),
        gpaTaskTitle: document.getElementById("gpaTaskTitle"),
        gpaStepsContainer: document.getElementById("gpaStepsContainer"),
        gpaEmpty: document.getElementById("gpaEmpty"),
        gpaModeBadge: document.getElementById("gpaModeBadge"),
        gpaStatsBar: document.getElementById("gpaStatsBar"),
        gpaStatsSteps: document.getElementById("gpaStatsSteps"),
        gpaStatsTime: document.getElementById("gpaStatsTime"),
        gpaStatsHealed: document.getElementById("gpaStatsHealed"),
    };

    // ── GPA Action Icons ───────────────────────────────────
    var GPA_ICONS = {
        navigate: "\uD83D\uDD17",
        click: "\uD83D\uDC46",
        type: "\u2328\uFE0F",
        scroll: "\uD83D\uDCC3",
        wait: "\u23F3",
        extract: "\uD83D\uDD0D",
        confirm: "\u2705",
    };

    // ── WebSocket Connection ───────────────────────────────

    function connectVoiceWs() {
        var protocol = location.protocol === "https:" ? "wss:" : "ws:";
        var url = protocol + "//" + location.host + "/ws/voice";

        state.voiceWs = new WebSocket(url);

        state.voiceWs.onopen = function () {
            console.log("Voice WebSocket connected");
            setConnectionStatus(true);
            state.reconnectDelay = 3000;
        };

        state.voiceWs.onmessage = function (event) {
            try {
                var msg = JSON.parse(event.data);
                handleVoiceMessage(msg);
            } catch (e) {
                console.error("Failed to parse voice message:", e);
            }
        };

        state.voiceWs.onclose = function () {
            console.log("Voice WebSocket closed");
            setConnectionStatus(false);
            scheduleReconnect(connectVoiceWs);
        };

        state.voiceWs.onerror = function (err) {
            console.error("Voice WebSocket error:", err);
        };
    }

    function connectScreenWs() {
        var protocol = location.protocol === "https:" ? "wss:" : "ws:";
        var url = protocol + "//" + location.host + "/ws/screen";

        state.screenWs = new WebSocket(url);

        state.screenWs.onopen = function () {
            console.log("Screen WebSocket connected");
        };

        state.screenWs.onmessage = function (event) {
            try {
                var msg = JSON.parse(event.data);
                handleScreenMessage(msg);
            } catch (e) {
                console.error("Failed to parse screen message:", e);
            }
        };

        state.screenWs.onclose = function () {
            console.log("Screen WebSocket closed");
            scheduleReconnect(connectScreenWs);
        };
    }

    function scheduleReconnect(connectFn) {
        setTimeout(function () {
            console.log("Attempting reconnect...");
            connectFn();
        }, state.reconnectDelay);
        state.reconnectDelay = Math.min(
            state.reconnectDelay * 2,
            state.maxReconnectDelay
        );
    }

    function setConnectionStatus(connected) {
        var badge = dom.connectionStatus;
        badge.className = "conn-badge " + (connected ? "connected" : "disconnected");
        dom.statusText.textContent = connected ? "Connected" : "Offline";
    }

    // ── Message Handlers ───────────────────────────────────

    function handleVoiceMessage(msg) {
        switch (msg.type) {
            case "audio":
                playAudio(msg.data, msg.mime_type);
                break;
            case "text":
                addTranscript("agent", msg.data);
                break;
            case "transcript":
                addTranscript(msg.role, msg.text);
                break;
            case "confirmation":
                showConfirmation(msg.prompt, msg.action);
                break;
            case "gpa_step":
                updateGPAPanel(msg);
                break;
            case "input_needed":
                showInputNeeded(msg.need_type, msg.message);
                break;
            case "click_preview":
                ScreenViewer.showClickPreview(
                    msg.x_pct, msg.y_pct, msg.action_type,
                    msg.description
                );
                break;
            case "plan_preview":
                showPlanPreview(msg.plan);
                break;
            case "upi_payment":
                ScreenViewer.showUPIPayment(msg);
                showToast("UPI Payment: Scan QR to pay Rs." + msg.amount, "warning", 6000);
                addTranscript("agent", "I found a payment of Rs." + msg.amount + " to " + msg.merchant_name + ". Scan the QR code with your UPI app to pay.");
                break;
            case "spending_blocked":
                showToast("Payment blocked: " + msg.message, "error", 6000);
                addTranscript("agent", "Payment of Rs." + msg.amount + " was blocked by your guardian. " + msg.message);
                break;
            case "screenshot_diff":
                ScreenViewer.showDiffBadge({
                    changed_percent: msg.diff.changed_percent,
                    tokens_saved: msg.tokens_saved,
                });
                break;
            case "safety_confirmation":
                ScreenViewer.showSafetyConfirmation(msg);
                showToast("Safety check — confirm before proceeding", "warning", 8000);
                break;
            case "safety_timeout":
                ScreenViewer.hideSafetyConfirmation();
                showToast("Action cancelled — no confirmation received", "error", 4000);
                break;
            case "speak_tts":
                speakText(msg.text);
                break;
        }
    }

    function handleScreenMessage(msg) {
        switch (msg.type) {
            case "screenshot":
                ScreenViewer.updateScreenshot(msg.data, msg.url, msg.step);
                break;
            case "action_overlay":
                ScreenViewer.showActionOverlay(msg.description);
                break;
            case "agent_reasoning":
                showAgentReasoning(msg.text, msg.step);
                break;
            case "gpa_step":
                updateGPAPanel(msg);
                break;
            case "input_needed":
                showInputNeeded(msg.need_type, msg.message);
                break;
            case "click_preview":
                ScreenViewer.showClickPreview(
                    msg.x_pct, msg.y_pct, msg.action_type,
                    msg.description
                );
                break;
            case "upi_payment":
                ScreenViewer.showUPIPayment(msg);
                break;
            case "screenshot_diff":
                ScreenViewer.showDiffBadge({
                    changed_percent: msg.diff.changed_percent,
                    tokens_saved: msg.tokens_saved,
                });
                break;
            case "safety_confirmation":
                ScreenViewer.showSafetyConfirmation(msg);
                break;
            case "safety_timeout":
                ScreenViewer.hideSafetyConfirmation();
                break;
            case "speak_tts":
                speakText(msg.text);
                break;
        }
    }

    // ── GPA Panel ──────────────────────────────────────────

    function updateGPAPanel(msg) {
        var step = msg.step;
        var taskDesc = msg.task_description || "";
        var isReplay = msg.is_replay || false;
        var stats = msg.stats;

        // New task — reset panel
        if (state.currentGpaTask !== msg.task_id) {
            state.currentGpaTask = msg.task_id;
            state.gpaSteps = {};
            dom.gpaStepsContainer.innerHTML = "";
            dom.gpaTaskTitle.textContent = taskDesc;
            dom.gpaStatsBar.style.display = "flex";

            if (isReplay) {
                dom.gpaModeBadge.textContent = "\u26A1 Replay";
                dom.gpaModeBadge.className = "gpa-mode-pill replay";
                dom.gpaModeBadge.style.display = "inline";
            } else {
                dom.gpaModeBadge.textContent = "\uD83E\uDD16 AI";
                dom.gpaModeBadge.className = "gpa-mode-pill ai";
                dom.gpaModeBadge.style.display = "inline";
            }
        }

        var card = state.gpaSteps[step.id];
        if (!card) {
            card = createStepCard(step);
            state.gpaSteps[step.id] = card;
            dom.gpaStepsContainer.appendChild(card);
        }

        updateStepCard(card, step);

        if (stats) {
            dom.gpaStatsSteps.textContent = stats.succeeded + "/" + stats.total;
            dom.gpaStatsTime.textContent = formatDuration(stats.total_time_ms);
            dom.gpaStatsHealed.textContent = String(stats.self_healed);
        }

        dom.gpaStepsContainer.scrollTop = dom.gpaStepsContainer.scrollHeight;
    }

    function createStepCard(step) {
        var card = document.createElement("div");
        card.className = "gpa-step-card";
        card.dataset.stepId = step.id;

        var icon = document.createElement("div");
        icon.className = "gpa-step-icon";
        var actionType = step.type || "click";
        icon.textContent = GPA_ICONS[actionType] || GPA_ICONS.click;
        card.appendChild(icon);

        var content = document.createElement("div");
        content.className = "gpa-step-content";

        var element = document.createElement("div");
        element.className = "gpa-step-element";
        element.textContent = step.element || step.type || "";
        content.appendChild(element);

        var detail = document.createElement("div");
        detail.className = "gpa-step-detail";
        detail.textContent = step.detail || "";
        content.appendChild(detail);

        card.appendChild(content);

        var meta = document.createElement("div");
        meta.className = "gpa-step-meta";
        card.appendChild(meta);

        return card;
    }

    function updateStepCard(card, step) {
        card.className = "gpa-step-card " + step.status;

        var element = card.querySelector(".gpa-step-element");
        if (element) {
            var prefix = step.is_replay ? "\u26A1 " : "";
            element.textContent = prefix + (step.element || step.type || "");
        }

        var detail = card.querySelector(".gpa-step-detail");
        if (detail) {
            detail.textContent = step.detail || "";
        }

        var meta = card.querySelector(".gpa-step-meta");
        if (meta) {
            meta.innerHTML = "";

            if (step.self_healed) {
                var healBadge = document.createElement("span");
                healBadge.className = "gpa-heal-badge";
                healBadge.textContent = "\uD83D\uDD27 Healed";
                healBadge.title = step.heal_description || "Auto-recovered";
                meta.appendChild(healBadge);
            }

            if (step.duration_ms != null) {
                var dur = document.createElement("span");
                dur.className = "gpa-step-duration";
                dur.textContent = formatDuration(step.duration_ms);
                meta.appendChild(dur);
            }

            var statusEl = document.createElement("span");
            statusEl.className = "gpa-step-status " + step.status;
            switch (step.status) {
                case "success":
                    statusEl.textContent = "\u2713";
                    break;
                case "failed":
                    statusEl.textContent = "\u2717";
                    statusEl.title = step.error || "Failed";
                    break;
                case "running":
                    statusEl.innerHTML = '<div class="spinner" style="width:12px;height:12px;border-width:2px;"></div>';
                    break;
                case "input":
                    statusEl.textContent = "\uD83D\uDD14";
                    break;
                default:
                    statusEl.textContent = "\u2022";
            }
            meta.appendChild(statusEl);
        }
    }

    function formatDuration(ms) {
        if (ms == null) return "";
        if (ms < 1000) return ms + "ms";
        return (ms / 1000).toFixed(1) + "s";
    }

    // ── Audio Playback (sequential queue) ──────────────────

    var nextPlayTime = 0;

    async function playAudio(base64Data, mimeType) {
        try {
            if (!state.playbackAudioContext) {
                state.playbackAudioContext = new AudioContext({ sampleRate: 24000 });
            }

            var ctx = state.playbackAudioContext;
            if (ctx.state === "suspended") {
                await ctx.resume();
            }

            var raw = atob(base64Data);
            var bytes = new Uint8Array(raw.length);
            for (var i = 0; i < raw.length; i++) {
                bytes[i] = raw.charCodeAt(i);
            }

            var pcm16 = new Int16Array(bytes.buffer);
            var float32 = new Float32Array(pcm16.length);
            for (var j = 0; j < pcm16.length; j++) {
                float32[j] = pcm16[j] / 32768.0;
            }

            var buffer = ctx.createBuffer(1, float32.length, 24000);
            buffer.getChannelData(0).set(float32);

            var now = ctx.currentTime;
            if (nextPlayTime < now) {
                nextPlayTime = now;
            }

            var source = ctx.createBufferSource();
            var gainNode = ctx.createGain();
            gainNode.gain.value = state.volume;

            source.buffer = buffer;
            source.connect(gainNode);
            gainNode.connect(ctx.destination);
            source.start(nextPlayTime);

            nextPlayTime += buffer.duration;

        } catch (e) {
            console.error("Audio playback error:", e);
        }
    }

    // ── Microphone ─────────────────────────────────────────

    async function startMicrophone() {
        try {
            if (!state.micAudioContext) {
                state.micAudioContext = new AudioContext({ sampleRate: 16000 });
            }

            state.micStream = await navigator.mediaDevices.getUserMedia({
                audio: {
                    sampleRate: 16000,
                    channelCount: 1,
                    echoCancellation: true,
                    noiseSuppression: true,
                },
            });

            var source = state.micAudioContext.createMediaStreamSource(state.micStream);

            await state.micAudioContext.audioWorklet.addModule("/static/js/audio-processor.js");
            state.micProcessor = new AudioWorkletNode(
                state.micAudioContext,
                "sahay-audio-processor"
            );

            state.micProcessor.port.onmessage = function (event) {
                try {
                    if (!event || !event.data || !event.data.pcmData) return;
                    var pcmData = event.data.pcmData;
                    if (!pcmData || typeof pcmData.buffer === "undefined") return;
                    if (state.voiceWs && state.voiceWs.readyState === WebSocket.OPEN) {
                        var base64 = arrayBufferToBase64(pcmData.buffer);
                        state.voiceWs.send(JSON.stringify({
                            type: "audio",
                            data: base64,
                        }));
                    }
                } catch (e) {
                    // Silently skip malformed audio frames
                }
            };

            source.connect(state.micProcessor);
            state.micProcessor.connect(state.micAudioContext.destination);

            state.micActive = true;
            dom.micButton.classList.add("active");
            dom.micButton.querySelector(".mic-label").textContent = "Listening...";

            // Reset audio queue so old agent speech stops
            nextPlayTime = 0;

            if (state.voiceWs && state.voiceWs.readyState === WebSocket.OPEN) {
                state.voiceWs.send(JSON.stringify({ type: "activity_start" }));
            }
        } catch (e) {
            console.error("Microphone error:", e);
            addTranscript("agent", "Could not access microphone. Please allow microphone access.");
        }
    }

    function stopMicrophone() {
        if (state.micProcessor) {
            state.micProcessor.disconnect();
            state.micProcessor = null;
        }
        if (state.micStream) {
            state.micStream.getTracks().forEach(function (t) { t.stop(); });
            state.micStream = null;
        }

        state.micActive = false;
        dom.micButton.classList.remove("active");
        dom.micButton.querySelector(".mic-label").textContent = "Tap to Speak";

        if (state.voiceWs && state.voiceWs.readyState === WebSocket.OPEN) {
            state.voiceWs.send(JSON.stringify({ type: "activity_end" }));
        }
    }

    function arrayBufferToBase64(buffer) {
        var bytes = new Uint8Array(buffer);
        var binary = "";
        for (var i = 0; i < bytes.byteLength; i++) {
            binary += String.fromCharCode(bytes[i]);
        }
        return btoa(binary);
    }

    // ── UI Updates ─────────────────────────────────────────

    function addTranscript(role, text) {
        if (!text || !text.trim()) return;

        var empty = dom.transcriptLog.querySelector(".transcript-empty");
        if (empty) empty.remove();

        var entry = document.createElement("div");
        entry.className = "transcript-entry " + (role === "user" ? "user" : "agent");

        var speaker = document.createElement("div");
        speaker.className = "transcript-speaker";
        speaker.textContent = role === "user" ? "You" : "SAHAY";

        var content = document.createElement("div");
        content.textContent = text;

        entry.appendChild(speaker);
        entry.appendChild(content);
        dom.transcriptLog.appendChild(entry);
        dom.transcriptLog.scrollTop = dom.transcriptLog.scrollHeight;

        if (role === "user") {
            detectAndSetLanguage(text);
        }
    }

    function showConfirmation(prompt, action) {
        dom.confirmationMessage.textContent = prompt;
        dom.confirmationDialog.style.display = "flex";
    }

    function hideConfirmation() {
        dom.confirmationDialog.style.display = "none";
    }

    function detectAndSetLanguage(text) {
        var lang = "EN";
        if (/[\u0900-\u097F]/.test(text)) lang = "HI";
        else if (/[\u0D00-\u0D7F]/.test(text)) lang = "ML";
        else if (/[\u0B80-\u0BFF]/.test(text)) lang = "TA";
        else if (/[\u0C00-\u0C7F]/.test(text)) lang = "TE";
        else if (/[\u0980-\u09FF]/.test(text)) lang = "BN";
        else if (/[\u0C80-\u0CFF]/.test(text)) lang = "KN";
        dom.languageIndicator.textContent = lang;
    }

    function sendTextMessage(text) {
        if (!text.trim()) return;
        if (state.voiceWs && state.voiceWs.readyState === WebSocket.OPEN) {
            state.voiceWs.send(JSON.stringify({
                type: "text",
                data: text,
            }));
            addTranscript("user", text);
            dom.textInput.value = "";
        } else {
            addTranscript("agent", "Not connected. Please wait...");
        }
    }

    // ── Event Listeners ────────────────────────────────────

    dom.micButton.addEventListener("click", function () {
        if (state.micActive) {
            stopMicrophone();
        } else {
            startMicrophone();
        }
    });

    dom.sendButton.addEventListener("click", function () {
        sendTextMessage(dom.textInput.value);
    });

    dom.textInput.addEventListener("keydown", function (e) {
        if (e.key === "Enter") {
            e.preventDefault();
            sendTextMessage(dom.textInput.value);
        }
    });

    dom.helpButton.addEventListener("click", function () {
        sendTextMessage(
            "What can you help me with? Please explain in simple words."
        );
    });

    dom.volumeSlider.addEventListener("input", function () {
        state.volume = parseInt(this.value, 10) / 100;
    });

    dom.confirmYes.addEventListener("click", function () {
        if (state.voiceWs && state.voiceWs.readyState === WebSocket.OPEN) {
            state.voiceWs.send(JSON.stringify({
                type: "confirmation_response",
                data: "yes",
            }));
        }
        hideConfirmation();
    });

    dom.confirmNo.addEventListener("click", function () {
        if (state.voiceWs && state.voiceWs.readyState === WebSocket.OPEN) {
            state.voiceWs.send(JSON.stringify({
                type: "confirmation_response",
                data: "no",
            }));
        }
        hideConfirmation();
    });

    // ── Input Needed (Agent asks user) ──────────────────────

    var INPUT_NEED_LABELS = {
        INPUT: "Info Needed",
        OTP: "OTP Required",
        CHOICE: "Your Choice Needed",
        CAPTCHA: "CAPTCHA Help",
        CLARIFICATION: "Please Clarify",
        CONFIRMATION: "Please Confirm",
    };

    var INPUT_NEED_ICONS = {
        INPUT: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M11 4H4a2 2 0 00-2 2v14a2 2 0 002 2h14a2 2 0 002-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 013 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>',
        OTP: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0110 0v4"/></svg>',
        CHOICE: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M16 3h5v5M4 20L21 3M21 16v5h-5M15 15l6 6M4 4l5 5"/></svg>',
        CAPTCHA: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>',
        CLARIFICATION: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><path d="M9.09 9a3 3 0 015.83 1c0 2-3 3-3 3M12 17h.01"/></svg>',
        CONFIRMATION: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg>',
    };

    // ═══════════════════════════════════════
    // PLAN PREVIEW — Shows planner's generated plan in GPA panel
    // ═══════════════════════════════════════

    // ═══════════════════════════════════════
    // AGENT REASONING — collapsible thought cards in GPA panel
    // ═══════════════════════════════════════
    var _reasoningCount = 0;

    function showAgentReasoning(text, step) {
        if (!text || text.length < 5) return;
        _reasoningCount++;

        // Classify the reasoning
        var isAction = text.includes("Click") || text.includes("Navigat") || text.includes("Typ") || text.includes("Fill");
        var isThinking = text.includes("look") || text.includes("see") || text.includes("found") || text.includes("search");
        var isResult = text.includes("TASK COMPLETE") || text.includes("TASK FAILED");
        var isNeed = text.startsWith("NEED ");

        // Skip if it's just a repeat or too short
        if (isNeed || isResult) return; // handled separately

        var icon = isAction ? "\uD83D\uDC46" : isThinking ? "\uD83E\uDDE0" : "\uD83D\uDCAC";
        var label = isAction ? "Action" : isThinking ? "Thinking" : "Agent";

        var card = document.createElement("div");
        card.className = "reasoning-card";

        var header = document.createElement("div");
        header.className = "reasoning-header";
        header.innerHTML = '<span class="reasoning-icon">' + icon + '</span>' +
            '<span class="reasoning-label">' + label + '</span>' +
            '<span class="reasoning-step">Step ' + (step || '?') + '</span>' +
            '<span class="reasoning-toggle">\u25BC</span>';

        var body = document.createElement("div");
        body.className = "reasoning-body";
        body.textContent = text.substring(0, 300);

        // Collapsed by default after 5 entries to keep panel clean
        if (_reasoningCount > 5) {
            body.style.display = "none";
            header.querySelector(".reasoning-toggle").textContent = "\u25B6";
        }

        header.addEventListener("click", function() {
            var isHidden = body.style.display === "none";
            body.style.display = isHidden ? "block" : "none";
            header.querySelector(".reasoning-toggle").textContent = isHidden ? "\u25BC" : "\u25B6";
        });

        card.appendChild(header);
        card.appendChild(body);
        dom.gpaStepsContainer.appendChild(card);
        dom.gpaStepsContainer.scrollTop = dom.gpaStepsContainer.scrollHeight;
    }

    function showPlanPreview(plan) {
        if (!plan) return;

        // Clear existing GPA steps
        dom.gpaStepsContainer.innerHTML = "";
        state.gpaSteps = {};

        // Show task title
        dom.gpaTaskTitle.textContent = plan.task_summary || "Task in progress...";
        dom.gpaStatsBar.style.display = "flex";

        // Confidence badge
        var confClass = plan.confidence === "high" ? "conf-high" :
                        plan.confidence === "medium" ? "conf-medium" : "conf-low";
        var confIcon = plan.confidence === "high" ? "\u2705" :
                       plan.confidence === "medium" ? "\u26A0\uFE0F" : "\u2753";

        // Plan header card
        var header = document.createElement("div");
        header.className = "plan-header-card";
        header.innerHTML =
            '<div class="plan-header-top">' +
                '<span class="plan-phase-badge planning">\uD83D\uDD0D Planner</span>' +
                '<span class="plan-conf ' + confClass + '">' + confIcon + ' ' + (plan.confidence || 'medium') + '</span>' +
            '</div>' +
            '<div class="plan-url">\uD83C\uDF10 ' + (plan.discovered_url || 'Searching...') + '</div>';
        dom.gpaStepsContainer.appendChild(header);

        // User inputs needed
        if (plan.user_inputs_needed && plan.user_inputs_needed.length > 0) {
            var inputsCard = document.createElement("div");
            inputsCard.className = "plan-inputs-card";
            inputsCard.innerHTML = '<div class="plan-inputs-title">\uD83D\uDCDD Inputs needed:</div>';
            plan.user_inputs_needed.forEach(function(inp) {
                var item = document.createElement("div");
                item.className = "plan-input-item";
                item.textContent = "\u2022 " + inp;
                inputsCard.appendChild(item);
            });
            dom.gpaStepsContainer.appendChild(inputsCard);
        }

        // Step list
        if (plan.steps) {
            plan.steps.forEach(function(step) {
                var stepCard = document.createElement("div");
                stepCard.className = "plan-step-card pending";
                stepCard.dataset.stepNum = step.step_number;

                var stepIcon = step.is_sensitive ? "\u26A0\uFE0F" :
                               step.action === "navigate" ? "\uD83C\uDF10" :
                               step.action === "input" ? "\u270D\uFE0F" :
                               step.action === "interact" ? "\uD83D\uDC46" :
                               step.action === "extract" ? "\uD83D\uDCCB" :
                               step.action === "wait" ? "\u23F3" :
                               step.action === "checkpoint" ? "\uD83D\uDEA9" : "\u25B6\uFE0F";

                stepCard.innerHTML =
                    '<div class="plan-step-num">' + step.step_number + '</div>' +
                    '<div class="plan-step-body">' +
                        '<div class="plan-step-desc">' + stepIcon + ' ' + step.description + '</div>' +
                        (step.needs_user_input ? '<div class="plan-step-tag input-tag">Needs input</div>' : '') +
                        (step.is_sensitive ? '<div class="plan-step-tag sensitive-tag">Sensitive</div>' : '') +
                    '</div>' +
                    '<div class="plan-step-status">\u23F3</div>';

                dom.gpaStepsContainer.appendChild(stepCard);
            });
        }

        // Stats
        dom.gpaStatsSteps.textContent = "0/" + (plan.steps ? plan.steps.length : 0);
        dom.gpaStatsTime.textContent = "0s";

        dom.gpaStepsContainer.scrollTop = 0;

        showToast("Plan ready: " + (plan.steps ? plan.steps.length : 0) + " steps", "success", 3000);
        addTranscript("agent", "Plan created: " + plan.task_summary + " (" + (plan.steps ? plan.steps.length : 0) + " steps)");
    }

    function showPlanningStep(step) {
        // Update plan step cards based on browser progress
        if (!step.phase) return;

        if (step.phase === "planning" || step.phase === "replanning") {
            // Add a planning status card
            var id = step.phase + "-" + Date.now();
            var statusClass = step.status === "completed" ? "completed" :
                              step.status === "failed" ? "failed" : "in_progress";
            var statusIcon = step.status === "completed" ? "\u2705" :
                             step.status === "failed" ? "\u274C" : "\uD83D\uDD04";

            var card = document.createElement("div");
            card.className = "plan-phase-card " + statusClass;
            card.innerHTML =
                '<div class="plan-phase-icon">' + statusIcon + '</div>' +
                '<div class="plan-phase-body">' +
                    '<div class="plan-phase-desc">' + step.description + '</div>' +
                    (step.detail ? '<div class="plan-phase-detail">' + step.detail + '</div>' : '') +
                '</div>';

            // Insert at top of GPA panel
            if (dom.gpaStepsContainer.firstChild) {
                dom.gpaStepsContainer.insertBefore(card, dom.gpaStepsContainer.firstChild);
            } else {
                dom.gpaStepsContainer.appendChild(card);
            }
        }
    }

    function showInputNeeded(needType, message) {
        var label = INPUT_NEED_LABELS[needType] || "Input Needed";
        var icon = INPUT_NEED_ICONS[needType] || INPUT_NEED_ICONS.INPUT;

        // Add special transcript entry
        var empty = dom.transcriptLog.querySelector(".transcript-empty");
        if (empty) empty.remove();

        var entry = document.createElement("div");
        entry.className = "transcript-entry agent input-needed-entry";

        var header = document.createElement("div");
        header.className = "input-needed-header";
        header.innerHTML = icon + ' <span class="input-needed-label">' + label + "</span>";

        var content = document.createElement("div");
        content.className = "input-needed-message";
        content.textContent = message;

        var hint = document.createElement("div");
        hint.className = "input-needed-hint";
        hint.textContent = "Speak or type your response below";

        entry.appendChild(header);
        entry.appendChild(content);
        entry.appendChild(hint);
        dom.transcriptLog.appendChild(entry);
        dom.transcriptLog.scrollTop = dom.transcriptLog.scrollHeight;

        // Show toast
        showToast(label + " — " + message.substring(0, 60), "warning", 5000);

        // Pulse the text input to draw attention
        dom.textInput.classList.add("input-pulse");
        dom.textInput.placeholder = "Type your response here...";
        dom.textInput.focus();
        setTimeout(function () {
            dom.textInput.classList.remove("input-pulse");
            dom.textInput.placeholder = "Type your request...";
        }, 4000);

        // Also pulse mic button
        dom.micButton.classList.add("needs-input");
        setTimeout(function () {
            dom.micButton.classList.remove("needs-input");
        }, 4000);
    }

    // ── Browser TTS (fallback when Live API can't speak) ────

    function speakText(text) {
        if (!text || !window.speechSynthesis) return;
        // Cancel any ongoing speech
        window.speechSynthesis.cancel();
        var utterance = new SpeechSynthesisUtterance(text);
        utterance.rate = 0.9;
        utterance.pitch = 1.0;
        utterance.volume = state.volume;
        // Try to use a Hindi voice if the text contains Hindi characters
        if (/[\u0900-\u097F]/.test(text)) {
            var voices = window.speechSynthesis.getVoices();
            var hindiVoice = voices.find(function(v) { return v.lang.startsWith("hi"); });
            if (hindiVoice) utterance.voice = hindiVoice;
        }
        window.speechSynthesis.speak(utterance);
    }

    // ── Toast Notifications ─────────────────────────────────

    var toastContainer = document.getElementById("toastContainer");

    function showToast(message, type, duration) {
        type = type || "info";
        duration = duration || 3000;
        var toast = document.createElement("div");
        toast.className = "toast " + type;
        toast.textContent = message;
        toastContainer.appendChild(toast);

        setTimeout(function () {
            toast.classList.add("removing");
            setTimeout(function () { toast.remove(); }, 250);
        }, duration);
    }

    // ── New Task ──────────────────────────────────────────

    var newTaskBtn = document.getElementById("newTaskBtn");

    function resetToNewTask() {
        // Reset browser view
        ScreenViewer.reset();

        // Reset GPA panel
        state.currentGpaTask = null;
        state.gpaSteps = {};
        dom.gpaStepsContainer.innerHTML = '<div class="gpa-empty" id="gpaEmpty">' +
            '<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="#CBD5E1" stroke-width="1.5"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>' +
            '<p>Actions will appear here in real-time</p></div>';
        dom.gpaTaskTitle.textContent = "Waiting for task...";
        dom.gpaStatsBar.style.display = "none";
        dom.gpaModeBadge.style.display = "none";

        // Reset audio
        nextPlayTime = 0;

        // Focus text input
        dom.textInput.value = "";
        dom.textInput.focus();

        showToast("Ready for a new task", "success");
    }

    newTaskBtn.addEventListener("click", resetToNewTask);

    // ── Stop Task ────────────────────────────────────────
    var stopTaskBtn = document.getElementById("stopTaskBtn");
    stopTaskBtn.addEventListener("click", function() {
        fetch("/api/task/stop", { method: "POST" })
            .then(function(r) { return r.json(); })
            .then(function(d) {
                showToast("Task stopped", "warning");
                stopTaskBtn.style.display = "none";
                takeoverBtn.style.display = "none";
            })
            .catch(function() { showToast("Failed to stop task", "error"); });
    });

    // ── Task Takeover (click on browser) ─────────────────
    var takeoverBtn = document.getElementById("takeoverBtn");
    var takeoverMode = false;

    takeoverBtn.addEventListener("click", function() {
        takeoverMode = !takeoverMode;
        takeoverBtn.classList.toggle("active", takeoverMode);
        var viewport = document.getElementById("browserViewport");
        viewport.style.cursor = takeoverMode ? "crosshair" : "default";
        if (takeoverMode) {
            showToast("Click on the browser screen to interact", "info", 3000);
        }
    });

    document.getElementById("browserViewport").addEventListener("click", function(e) {
        if (!takeoverMode) return;
        var img = document.getElementById("screenImage");
        if (!img || img.style.display === "none") return;

        var rect = img.getBoundingClientRect();
        var x_pct = ((e.clientX - rect.left) / rect.width) * 100;
        var y_pct = ((e.clientY - rect.top) / rect.height) * 100;

        // Show click indicator
        ScreenViewer.showClickPreview(x_pct, y_pct, "click", "Your click");

        fetch("/api/takeover/click", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ x_pct: x_pct, y_pct: y_pct })
        })
        .then(function(r) { return r.json(); })
        .then(function(d) {
            if (d.status === "clicked") {
                showToast("Clicked at (" + d.x + ", " + d.y + ")", "success", 2000);
            }
        });
    });

    // Show/hide stop & takeover buttons when task starts/ends
    function showTaskControls() {
        stopTaskBtn.style.display = "inline-flex";
        takeoverBtn.style.display = "inline-flex";
    }
    function hideTaskControls() {
        stopTaskBtn.style.display = "none";
        takeoverBtn.style.display = "none";
        takeoverMode = false;
        document.getElementById("browserViewport").style.cursor = "default";
        takeoverBtn.classList.remove("active");
        ScreenViewer.hideThinking();
    }

    // Hook into screenshot messages to show controls
    var _origHandleScreen = handleScreenMessage;
    handleScreenMessage = function(msg) {
        if (msg.type === "screenshot") showTaskControls();
        if (msg.type === "action_overlay" && msg.description &&
            (msg.description.includes("stopped") || msg.description.includes("finished") || msg.description.includes("complete"))) {
            hideTaskControls();
        }
        _origHandleScreen(msg);
    };

    // Escape key stops task
    document.addEventListener("keydown", function(e) {
        if (e.key === "Escape" && stopTaskBtn.style.display !== "none") {
            stopTaskBtn.click();
        }
    });

    // ── Clear GPA Log ─────────────────────────────────────

    var clearGpaBtn = document.getElementById("clearGpaBtn");

    clearGpaBtn.addEventListener("click", function () {
        state.currentGpaTask = null;
        state.gpaSteps = {};
        dom.gpaStepsContainer.innerHTML = '<div class="gpa-empty" id="gpaEmpty">' +
            '<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="#CBD5E1" stroke-width="1.5"><circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/></svg>' +
            '<p>Actions will appear here in real-time</p></div>';
        dom.gpaTaskTitle.textContent = "Waiting for task...";
        dom.gpaStatsBar.style.display = "none";
        dom.gpaModeBadge.style.display = "none";
        showToast("Action log cleared", "info");
    });

    // ── Clear Transcript ──────────────────────────────────

    var clearTranscriptBtn = document.getElementById("clearTranscriptBtn");

    clearTranscriptBtn.addEventListener("click", function () {
        dom.transcriptLog.innerHTML = '<div class="transcript-empty">Say something to start...</div>';
        showToast("Conversation cleared", "info");
    });

    // ── Copy Transcript ───────────────────────────────────

    var copyTranscriptBtn = document.getElementById("copyTranscriptBtn");

    copyTranscriptBtn.addEventListener("click", function () {
        var entries = dom.transcriptLog.querySelectorAll(".transcript-entry");
        if (entries.length === 0) {
            showToast("Nothing to copy", "warning");
            return;
        }
        var lines = [];
        entries.forEach(function (entry) {
            var speaker = entry.querySelector(".transcript-speaker");
            var content = entry.querySelector("div:last-child");
            if (speaker && content) {
                lines.push(speaker.textContent + ": " + content.textContent);
            }
        });
        navigator.clipboard.writeText(lines.join("\n")).then(function () {
            showToast("Transcript copied!", "success");
        }).catch(function () {
            showToast("Copy failed", "error");
        });
    });

    // ── Fullscreen Toggle ─────────────────────────────────

    var fullscreenBtn = document.getElementById("fullscreenBtn");
    var browserPanel = document.querySelector(".browser-panel");

    fullscreenBtn.addEventListener("click", function () {
        browserPanel.classList.toggle("fullscreen");
    });

    // ── Keyboard Shortcuts ────────────────────────────────

    document.addEventListener("keydown", function (e) {
        // Escape: close fullscreen or confirmation dialog
        if (e.key === "Escape") {
            if (dom.confirmationDialog.style.display !== "none") {
                hideConfirmation();
            } else if (browserPanel.classList.contains("fullscreen")) {
                browserPanel.classList.remove("fullscreen");
            }
        }

        // Ctrl+N: new task
        if ((e.ctrlKey || e.metaKey) && e.key === "n") {
            e.preventDefault();
            resetToNewTask();
        }

        // F key: toggle fullscreen (only when not typing)
        if (e.key === "f" && document.activeElement !== dom.textInput) {
            browserPanel.classList.toggle("fullscreen");
        }

        // Ctrl+Enter: send message
        if ((e.ctrlKey || e.metaKey) && e.key === "Enter" && dom.textInput.value.trim()) {
            e.preventDefault();
            sendTextMessage(dom.textInput.value);
        }
    });

    // ── Enhanced Transcript with Delete ───────────────────

    // Override addTranscript to add delete buttons
    var _origAddTranscript = addTranscript;
    addTranscript = function (role, text) {
        if (!text || !text.trim()) return;

        var empty = dom.transcriptLog.querySelector(".transcript-empty");
        if (empty) empty.remove();

        var entry = document.createElement("div");
        entry.className = "transcript-entry " + (role === "user" ? "user" : "agent");

        var speaker = document.createElement("div");
        speaker.className = "transcript-speaker";
        speaker.textContent = role === "user" ? "You" : "SAHAY";

        var content = document.createElement("div");
        content.textContent = text;

        var deleteBtn = document.createElement("button");
        deleteBtn.className = "te-delete";
        deleteBtn.innerHTML = "\u2715";
        deleteBtn.title = "Remove";
        deleteBtn.addEventListener("click", function () {
            entry.classList.add("removing");
            setTimeout(function () { entry.remove(); }, 250);
        });

        entry.appendChild(speaker);
        entry.appendChild(content);
        entry.appendChild(deleteBtn);
        dom.transcriptLog.appendChild(entry);
        dom.transcriptLog.scrollTop = dom.transcriptLog.scrollHeight;

        if (role === "user") {
            detectAndSetLanguage(text);
        }
    };

    // ── Connection Toast ──────────────────────────────────

    var _origSetConn = setConnectionStatus;
    setConnectionStatus = function (connected) {
        var wasPreviouslyConnected = dom.connectionStatus.classList.contains("connected");
        _origSetConn(connected);

        if (connected && !wasPreviouslyConnected) {
            showToast("Connected to SAHAY", "success", 2000);
        } else if (!connected && wasPreviouslyConnected) {
            showToast("Connection lost. Reconnecting...", "warning", 4000);
        }
    };

    // ── Suggestion Chips ───────────────────────────────────

    var chips = document.querySelectorAll(".suggestion-chip");
    chips.forEach(function (chip) {
        chip.addEventListener("click", function () {
            var text = chip.getAttribute("data-text") || chip.textContent.trim();
            dom.textInput.value = text;
            sendTextMessage(text);
        });
    });

    // ── Initialize ─────────────────────────────────────────

    connectVoiceWs();
    connectScreenWs();
})();
