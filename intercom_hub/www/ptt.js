/**
 * Intercom PTT Web Client
 *
 * Connects to the Intercom Hub add-on via WebSocket and provides
 * push-to-talk functionality from any web browser.
 */

class IntercomPTT {
    constructor() {
        // State
        this.audioContext = null;
        this.mediaStream = null;
        this.workletNode = null;
        this.websocket = null;
        this.isConnected = false;
        this.isTransmitting = false;
        this.isReceiving = false;

        // Audio playback
        this.playbackQueue = [];
        this.nextPlayTime = 0;

        // UI Elements
        this.initOverlay = document.getElementById('initOverlay');
        this.initButton = document.getElementById('initButton');
        this.pttButton = document.getElementById('pttButton');
        this.connStatus = document.getElementById('connStatus');
        this.pttStatus = document.getElementById('pttStatus');
        this.targetSelect = document.getElementById('targetSelect');
        this.errorBanner = document.getElementById('errorBanner');

        // Bind event handlers
        this.initButton.addEventListener('click', () => this.initialize());

        // PTT button - support both mouse and touch
        this.pttButton.addEventListener('mousedown', (e) => this.startTransmit(e));
        this.pttButton.addEventListener('mouseup', (e) => this.stopTransmit(e));
        this.pttButton.addEventListener('mouseleave', (e) => this.stopTransmit(e));

        this.pttButton.addEventListener('touchstart', (e) => {
            e.preventDefault();
            this.startTransmit(e);
        });
        this.pttButton.addEventListener('touchend', (e) => {
            e.preventDefault();
            this.stopTransmit(e);
        });
        this.pttButton.addEventListener('touchcancel', (e) => {
            e.preventDefault();
            this.stopTransmit(e);
        });

        // Target selection
        this.targetSelect.addEventListener('change', () => this.sendTargetChange());

        // Keyboard support (spacebar)
        document.addEventListener('keydown', (e) => {
            if (e.code === 'Space' && !e.repeat && this.isConnected) {
                e.preventDefault();
                this.startTransmit(e);
            }
        });
        document.addEventListener('keyup', (e) => {
            if (e.code === 'Space') {
                e.preventDefault();
                this.stopTransmit(e);
            }
        });
    }

    showError(message) {
        this.errorBanner.textContent = message;
        this.errorBanner.classList.add('visible');
    }

    hideError() {
        this.errorBanner.classList.remove('visible');
    }

    updateStatus(status) {
        this.pttStatus.textContent = status.charAt(0).toUpperCase() + status.slice(1);
        this.pttStatus.className = 'status-value status-' + status;

        // Update button state
        this.pttButton.classList.remove('transmitting', 'receiving', 'busy');
        if (status === 'transmitting') {
            this.pttButton.classList.add('transmitting');
        } else if (status === 'receiving') {
            this.pttButton.classList.add('receiving');
        }
    }

    async initialize() {
        try {
            // Check if we're in a secure context (HTTPS or localhost)
            const isSecure = window.isSecureContext;

            if (isSecure) {
                // Request microphone permission
                this.mediaStream = await navigator.mediaDevices.getUserMedia({
                    audio: {
                        echoCancellation: true,
                        noiseSuppression: true,
                        autoGainControl: true,
                        sampleRate: 16000,
                        channelCount: 1
                    }
                });

                // Create audio context (16kHz to match protocol)
                this.audioContext = new AudioContext({ sampleRate: 16000 });

                // Load AudioWorklet
                await this.audioContext.audioWorklet.addModule('ptt-processor.js');

                // Create worklet node
                this.workletNode = new AudioWorkletNode(this.audioContext, 'ptt-processor');

                // Handle audio frames from worklet
                this.workletNode.port.onmessage = (event) => {
                    if (event.data.type === 'audio' && this.isConnected && this.isTransmitting) {
                        this.sendAudio(event.data.pcm);
                    }
                };

                // Connect microphone to worklet
                const source = this.audioContext.createMediaStreamSource(this.mediaStream);
                source.connect(this.workletNode);

                this.micEnabled = true;
            } else {
                // No mic access over HTTP - show warning but continue
                console.warn('Microphone requires HTTPS. Receive-only mode.');
                this.showError('Microphone requires HTTPS. You can listen but not talk.');
                this.micEnabled = false;

                // Still create audio context for playback
                this.audioContext = new AudioContext({ sampleRate: 16000 });
            }

            // Hide init overlay
            this.initOverlay.classList.add('hidden');

            // Connect WebSocket
            this.connectWebSocket();

        } catch (error) {
            console.error('Initialization error:', error);
            if (error.name === 'NotAllowedError') {
                this.showError('Microphone permission denied. Please allow access and reload.');
            } else if (error.name === 'NotFoundError') {
                this.showError('No microphone found. Please connect a microphone.');
            } else if (error.name === 'NotSupportedError') {
                this.showError('Microphone requires HTTPS connection.');
            } else {
                this.showError('Failed to initialize: ' + error.message);
            }

            // Still try to connect for receive-only
            this.initOverlay.classList.add('hidden');
            this.micEnabled = false;
            this.connectWebSocket();
        }
    }

    connectWebSocket() {
        // Determine WebSocket URL - use relative path for ingress compatibility
        const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
        // Get the base path (ingress adds a prefix like /api/hassio_ingress/<token>/)
        const basePath = window.location.pathname.replace(/\/$/, '');
        // Use /ws to avoid conflict with HA's /api/websocket endpoint
        const wsUrl = `${protocol}//${window.location.host}${basePath}/ws`;

        console.log('Connecting to WebSocket:', wsUrl);

        this.websocket = new WebSocket(wsUrl);
        this.websocket.binaryType = 'arraybuffer';

        this.websocket.onopen = () => {
            console.log('WebSocket connected');
            this.isConnected = true;
            this.connStatus.textContent = 'Connected';
            this.connStatus.className = 'status-value status-connected';
            this.pttButton.classList.remove('disabled');
            this.hideError();

            // Request current state and target list
            this.websocket.send(JSON.stringify({ type: 'get_state' }));
        };

        this.websocket.onclose = () => {
            console.log('WebSocket disconnected');
            this.isConnected = false;
            this.connStatus.textContent = 'Disconnected';
            this.connStatus.className = 'status-value status-disconnected';
            this.pttButton.classList.add('disabled');
            this.updateStatus('idle');

            // Reconnect after delay
            setTimeout(() => this.connectWebSocket(), 3000);
        };

        this.websocket.onerror = (error) => {
            console.error('WebSocket error:', error);
            this.showError('Connection error. Retrying...');
        };

        this.websocket.onmessage = (event) => {
            if (event.data instanceof ArrayBuffer) {
                // Binary data = audio PCM
                this.playAudio(event.data);
            } else {
                // JSON message
                try {
                    const msg = JSON.parse(event.data);
                    this.handleMessage(msg);
                } catch (e) {
                    console.error('Failed to parse message:', e);
                }
            }
        };
    }

    handleMessage(msg) {
        switch (msg.type) {
            case 'state':
                this.updateStatus(msg.status);
                if (msg.status === 'receiving') {
                    this.isReceiving = true;
                } else {
                    this.isReceiving = false;
                }
                break;

            case 'targets':
                // Update target dropdown
                this.targetSelect.innerHTML = '<option value="all">All Rooms</option>';
                if (msg.rooms) {
                    msg.rooms.forEach(room => {
                        const option = document.createElement('option');
                        option.value = room;
                        option.textContent = room;
                        this.targetSelect.appendChild(option);
                    });
                }
                break;

            case 'busy':
                // Channel is busy
                this.pttButton.classList.add('busy');
                this.updateStatus('busy');
                break;

            case 'error':
                this.showError(msg.message);
                break;
        }
    }

    startTransmit(event) {
        if (!this.isConnected || this.isTransmitting) return;

        // Check if mic is available
        if (!this.micEnabled) {
            this.showError('Microphone requires HTTPS connection.');
            return;
        }

        // Check if receiving (channel busy)
        if (this.isReceiving) {
            this.pttButton.classList.add('busy');
            return;
        }

        this.isTransmitting = true;
        this.pttButton.classList.add('active', 'transmitting');
        this.updateStatus('transmitting');

        // Tell worklet to start capturing
        if (this.workletNode) {
            this.workletNode.port.postMessage({ type: 'ptt', active: true });
        }

        // Tell server we're transmitting
        if (this.websocket && this.websocket.readyState === WebSocket.OPEN) {
            this.websocket.send(JSON.stringify({
                type: 'ptt_start',
                target: this.targetSelect.value
            }));
        }
    }

    stopTransmit(event) {
        if (!this.isTransmitting) {
            this.pttButton.classList.remove('busy');
            return;
        }

        this.isTransmitting = false;
        this.pttButton.classList.remove('active', 'transmitting', 'busy');
        this.updateStatus('idle');

        // Tell worklet to stop capturing
        if (this.workletNode) {
            this.workletNode.port.postMessage({ type: 'ptt', active: false });
        }

        // Tell server we stopped
        if (this.websocket && this.websocket.readyState === WebSocket.OPEN) {
            this.websocket.send(JSON.stringify({ type: 'ptt_stop' }));
        }
    }

    sendAudio(pcmBuffer) {
        if (this.websocket && this.websocket.readyState === WebSocket.OPEN) {
            this.websocket.send(pcmBuffer);
        }
    }

    sendTargetChange() {
        if (this.websocket && this.websocket.readyState === WebSocket.OPEN) {
            this.websocket.send(JSON.stringify({
                type: 'set_target',
                target: this.targetSelect.value
            }));
        }
    }

    playAudio(pcmBuffer) {
        if (!this.audioContext) return;

        // Convert Int16 to Float32
        const int16 = new Int16Array(pcmBuffer);
        const float32 = new Float32Array(int16.length);

        for (let i = 0; i < int16.length; i++) {
            float32[i] = int16[i] / 32768.0;
        }

        // Create audio buffer
        const buffer = this.audioContext.createBuffer(1, float32.length, 16000);
        buffer.getChannelData(0).set(float32);

        // Create source and schedule playback
        const source = this.audioContext.createBufferSource();
        source.buffer = buffer;
        source.connect(this.audioContext.destination);

        // Schedule to play in sequence
        const now = this.audioContext.currentTime;
        if (this.nextPlayTime < now) {
            this.nextPlayTime = now + 0.02; // Small buffer
        }
        source.start(this.nextPlayTime);
        this.nextPlayTime += buffer.duration;
    }
}

// Initialize when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    window.intercomPTT = new IntercomPTT();
});
