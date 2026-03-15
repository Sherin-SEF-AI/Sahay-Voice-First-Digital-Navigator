/**
 * SAHAY Screen Viewer
 *
 * Handles browser screenshot display with smooth transitions,
 * action overlays, click previews, UPI QR display, and loading states.
 */

var ScreenViewer = (function () {
    "use strict";

    var screenImage = document.getElementById("screenImage");
    var placeholderMessage = document.getElementById("placeholderMessage");
    var actionOverlay = document.getElementById("actionOverlay");
    var actionText = document.getElementById("actionText");
    var loadingSpinner = document.getElementById("loadingSpinner");
    var currentUrl = document.getElementById("currentUrl");
    var browserViewport = document.getElementById("browserViewport");

    var overlayTimeout = null;
    var hasReceivedScreenshot = false;
    var clickPreviewEl = null;
    var clickLabelEl = null;
    var upiOverlayEl = null;
    var diffBadgeEl = null;
    var safetyOverlayEl = null;
    var thinkingEl = null;
    var thinkingTimeout = null;

    // Create click preview indicator (pulsing red circle)
    function _createClickPreview() {
        if (clickPreviewEl) return;

        clickPreviewEl = document.createElement("div");
        clickPreviewEl.className = "click-preview-indicator";
        clickPreviewEl.innerHTML =
            '<div class="click-ring click-ring-1"></div>' +
            '<div class="click-ring click-ring-2"></div>' +
            '<div class="click-dot"></div>';
        clickPreviewEl.style.display = "none";
        browserViewport.appendChild(clickPreviewEl);

        clickLabelEl = document.createElement("div");
        clickLabelEl.className = "click-preview-label";
        clickLabelEl.style.display = "none";
        browserViewport.appendChild(clickLabelEl);
    }

    // Create UPI QR overlay
    function _createUPIOverlay() {
        if (upiOverlayEl) return;

        upiOverlayEl = document.createElement("div");
        upiOverlayEl.className = "upi-overlay";
        upiOverlayEl.style.display = "none";
        browserViewport.appendChild(upiOverlayEl);
    }

    // Create diff badge
    function _createDiffBadge() {
        if (diffBadgeEl) return;

        diffBadgeEl = document.createElement("div");
        diffBadgeEl.className = "diff-badge";
        diffBadgeEl.style.display = "none";
        browserViewport.appendChild(diffBadgeEl);
    }

    _createClickPreview();
    _createUPIOverlay();
    _createDiffBadge();

    /**
     * Update the browser view with a new screenshot.
     */
    function _createThinkingPulse() {
        if (thinkingEl) return;
        thinkingEl = document.createElement("div");
        thinkingEl.className = "thinking-pulse";
        thinkingEl.innerHTML =
            '<div class="thinking-brain">\uD83E\uDDE0</div>' +
            '<div class="thinking-text">Analyzing...</div>' +
            '<div class="thinking-dots"><span></span><span></span><span></span></div>';
        browserViewport.appendChild(thinkingEl);
    }

    function showThinking() {
        _createThinkingPulse();
        thinkingEl.style.display = "flex";
        // Auto-hide after 15s in case screenshot never comes
        clearTimeout(thinkingTimeout);
        thinkingTimeout = setTimeout(function() {
            hideThinking();
        }, 15000);
    }

    function hideThinking() {
        if (thinkingEl) thinkingEl.style.display = "none";
        clearTimeout(thinkingTimeout);
    }

    function updateScreenshot(base64Png, url, step) {
        if (!hasReceivedScreenshot) {
            hasReceivedScreenshot = true;
            placeholderMessage.style.display = "none";
            screenImage.style.display = "block";
        }

        loadingSpinner.style.display = "none";
        hideThinking();

        screenImage.style.opacity = "0.7";
        screenImage.src = "data:image/png;base64," + base64Png;
        screenImage.onload = function () {
            screenImage.style.opacity = "1";
            // Show thinking after screenshot loads — agent is processing next step
            showThinking();
        };

        if (url) {
            currentUrl.textContent = url;
        }
    }

    /**
     * Get the actual rendered bounds of the screenshot image within the viewport.
     * Accounts for object-fit: contain letterboxing.
     */
    function _getImageBounds() {
        var vpRect = browserViewport.getBoundingClientRect();
        var vpW = vpRect.width;
        var vpH = vpRect.height;

        var imgNatW = screenImage.naturalWidth || 1440;
        var imgNatH = screenImage.naturalHeight || 900;
        var imgRatio = imgNatW / imgNatH;
        var vpRatio = vpW / vpH;

        var renderW, renderH, offsetX, offsetY;
        if (vpRatio > imgRatio) {
            // Viewport wider — image fits height, letterboxed left/right
            renderH = vpH;
            renderW = vpH * imgRatio;
            offsetX = (vpW - renderW) / 2;
            offsetY = 0;
        } else {
            // Viewport taller — image fits width, letterboxed top/bottom
            renderW = vpW;
            renderH = vpW / imgRatio;
            offsetX = 0;
            offsetY = (vpH - renderH) / 2;
        }
        return { renderW: renderW, renderH: renderH, offsetX: offsetX, offsetY: offsetY, vpW: vpW, vpH: vpH };
    }

    /**
     * Show a visual click preview at the target coordinates.
     * Precisely positioned on the actual screenshot image bounds.
     */
    function showClickPreview(xPct, yPct, actionType, description) {
        var bounds = _getImageBounds();

        // Convert from % of screenshot to pixel position on viewport
        var pixelX = bounds.offsetX + (xPct / 100) * bounds.renderW;
        var pixelY = bounds.offsetY + (yPct / 100) * bounds.renderH;

        // Convert to % of viewport for CSS positioning
        var adjustedXPct = (pixelX / bounds.vpW) * 100;
        var adjustedYPct = (pixelY / bounds.vpH) * 100;

        clickPreviewEl.style.left = adjustedXPct + "%";
        clickPreviewEl.style.top = adjustedYPct + "%";
        clickPreviewEl.style.display = "block";
        clickPreviewEl.className = "click-preview-indicator " + actionType;

        // Position the label
        var labelY = adjustedYPct > 85 ? adjustedYPct - 6 : adjustedYPct + 4;
        clickLabelEl.style.left = adjustedXPct + "%";
        clickLabelEl.style.top = labelY + "%";
        clickLabelEl.textContent = (actionType === "click" ? "Clicking: " : "Typing: ") + description;
        clickLabelEl.style.display = "block";

        // Auto-hide after animation
        setTimeout(function () {
            clickPreviewEl.style.display = "none";
            clickLabelEl.style.display = "none";
        }, 1500);
    }

    /**
     * Show UPI QR code overlay on the browser view.
     */
    function showUPIPayment(data) {
        upiOverlayEl.innerHTML =
            '<div class="upi-card">' +
                '<div class="upi-header">' +
                    '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="#E8552D" stroke-width="2"><rect x="1" y="4" width="22" height="16" rx="2"/><line x1="1" y1="10" x2="23" y2="10"/></svg>' +
                    '<span>UPI Payment</span>' +
                    '<button class="upi-close" onclick="ScreenViewer.hideUPIPayment()">&times;</button>' +
                '</div>' +
                '<div class="upi-qr-wrap">' +
                    '<img src="data:image/png;base64,' + data.qr_code + '" class="upi-qr-img" alt="UPI QR Code">' +
                '</div>' +
                '<div class="upi-amount">&#8377;' + data.amount.toLocaleString("en-IN", {minimumFractionDigits: 2}) + '</div>' +
                '<div class="upi-merchant">to ' + data.merchant_name + '</div>' +
                '<div class="upi-hint">Scan with Google Pay, PhonePe, or Paytm</div>' +
                '<a href="' + data.deep_link + '" class="upi-open-btn" target="_blank">Open UPI App</a>' +
            '</div>';
        upiOverlayEl.style.display = "flex";
    }

    /**
     * Hide UPI QR overlay.
     */
    function hideUPIPayment() {
        upiOverlayEl.style.display = "none";
    }

    /**
     * Show screenshot diff badge.
     */
    function showDiffBadge(diffData) {
        diffBadgeEl.innerHTML =
            '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M13 2L3 14h9l-1 8 10-12h-9l1-8z"/></svg>' +
            '<span>' + diffData.changed_percent + '% changed</span>' +
            (diffData.tokens_saved ? '<span class="diff-saved">' + diffData.tokens_saved + ' tokens saved</span>' : '');
        diffBadgeEl.style.display = "flex";

        setTimeout(function () {
            diffBadgeEl.style.display = "none";
        }, 4000);
    }

    /**
     * Show an action description overlay.
     */
    function showActionOverlay(description) {
        if (!description) return;

        actionText.textContent = description;
        actionOverlay.style.display = "block";

        if (overlayTimeout) {
            clearTimeout(overlayTimeout);
        }
        overlayTimeout = setTimeout(function () {
            actionOverlay.style.display = "none";
            overlayTimeout = null;
        }, 5000);
    }

    function hideActionOverlay() {
        actionOverlay.style.display = "none";
        if (overlayTimeout) {
            clearTimeout(overlayTimeout);
            overlayTimeout = null;
        }
    }

    function showLoading() {
        loadingSpinner.style.display = "flex";
    }

    function hideLoading() {
        loadingSpinner.style.display = "none";
    }

    function reset() {
        hasReceivedScreenshot = false;
        screenImage.style.display = "none";
        placeholderMessage.style.display = "block";
        loadingSpinner.style.display = "none";
        actionOverlay.style.display = "none";
        currentUrl.textContent = "";
        if (clickPreviewEl) clickPreviewEl.style.display = "none";
        if (clickLabelEl) clickLabelEl.style.display = "none";
        if (upiOverlayEl) upiOverlayEl.style.display = "none";
        if (diffBadgeEl) diffBadgeEl.style.display = "none";
    }

    /**
     * Show safety gate confirmation overlay.
     */
    function showSafetyConfirmation(data) {
        hideSafetyConfirmation();

        safetyOverlayEl = document.createElement("div");
        safetyOverlayEl.className = "safety-overlay";
        safetyOverlayEl.innerHTML =
            '<div class="safety-card">' +
                '<div class="safety-icon">' +
                    '<svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="#f59e0b" stroke-width="2">' +
                        '<path d="M12 9v4m0 4h.01M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/>' +
                    '</svg>' +
                '</div>' +
                '<div class="safety-title">Safety Confirmation Required</div>' +
                '<div class="safety-prompt">' + (data.prompt || data.action) + '</div>' +
                '<div class="safety-hint">Say "Yes" or "No" to confirm, or use the buttons below</div>' +
                '<div class="safety-buttons">' +
                    '<button class="safety-btn safety-btn-deny" onclick="ScreenViewer.respondSafetyGate(false)">Cancel</button>' +
                    '<button class="safety-btn safety-btn-approve" onclick="ScreenViewer.respondSafetyGate(true)">Yes, Proceed</button>' +
                '</div>' +
                '<div class="safety-timer"><div class="safety-timer-bar"></div></div>' +
            '</div>';

        browserViewport.appendChild(safetyOverlayEl);
    }

    /**
     * Hide safety gate confirmation overlay.
     */
    function hideSafetyConfirmation() {
        if (safetyOverlayEl) {
            safetyOverlayEl.remove();
            safetyOverlayEl = null;
        }
    }

    /**
     * Send safety gate response to backend.
     */
    function respondSafetyGate(approved) {
        // Instant visual feedback BEFORE hiding
        if (safetyOverlayEl) {
            var card = safetyOverlayEl.querySelector(".safety-card");
            if (card) {
                card.innerHTML = approved
                    ? '<div style="text-align:center;padding:30px;"><div style="font-size:48px;margin-bottom:10px;">&#x2705;</div><div style="font-size:18px;font-weight:600;color:#22c55e;">Approved! Proceeding...</div></div>'
                    : '<div style="text-align:center;padding:30px;"><div style="font-size:48px;margin-bottom:10px;">&#x274C;</div><div style="font-size:18px;font-weight:600;color:#ef4444;">Cancelled</div></div>';
            }
        }

        fetch("/api/safety-gate/respond", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ approved: approved }),
        }).catch(function (err) {
            console.error("Safety gate response error:", err);
        });

        // Hide after brief delay so user sees the feedback
        setTimeout(function() {
            hideSafetyConfirmation();
        }, 800);
    }

    return {
        updateScreenshot: updateScreenshot,
        showClickPreview: showClickPreview,
        showUPIPayment: showUPIPayment,
        hideUPIPayment: hideUPIPayment,
        showDiffBadge: showDiffBadge,
        showActionOverlay: showActionOverlay,
        hideActionOverlay: hideActionOverlay,
        showLoading: showLoading,
        hideLoading: hideLoading,
        showSafetyConfirmation: showSafetyConfirmation,
        hideSafetyConfirmation: hideSafetyConfirmation,
        respondSafetyGate: respondSafetyGate,
        showThinking: showThinking,
        hideThinking: hideThinking,
        reset: reset,
    };
})();
