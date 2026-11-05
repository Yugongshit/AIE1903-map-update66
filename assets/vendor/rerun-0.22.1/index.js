let get_wasm_bindgen = null;
let _wasm_module = null;
/*<INLINE-MARKER>*/
async function fetch_viewer_js() {
    return (await import("./re_viewer.js")).default;
}
async function fetch_viewer_wasm() {
    return fetch(new URL("./re_viewer_bg.wasm", import.meta.url));
}
/*<INLINE-MARKER>*/
async function load() {
    // instantiate wbg globals+module for every invocation of `load`,
    // but don't load the JS/Wasm source every time
    if (!get_wasm_bindgen || !_wasm_module) {
        [get_wasm_bindgen, _wasm_module] = await Promise.all([
            fetch_viewer_js(),
            WebAssembly.compileStreaming(fetch_viewer_wasm()),
        ]);
    }
    let bindgen = get_wasm_bindgen();
    await bindgen(_wasm_module);
    return class extends bindgen.WebHandle {
        free() {
            super.free();
            // @ts-expect-error
            bindgen.deinit();
        }
    };
}
let _minimize_current_fullscreen_viewer = null;
function randomId() {
    const bytes = new Uint8Array(16);
    crypto.getRandomValues(bytes);
    return Array.from(bytes)
        .map((byte) => byte.toString(16).padStart(2, "0"))
        .join("");
}
function delay(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
}
export class WebViewer {
    #id = randomId();
    // NOTE: Using the handle requires wrapping all calls to its methods in try/catch.
    //       On failure, call `this.stop` to prevent a memory leak, then re-throw the error.
    #handle = null;
    #canvas = null;
    #state = "stopped";
    #fullscreen = false;
    #allow_fullscreen = false;
    constructor() {
        injectStyle();
        setupGlobalEventListeners();
    }
    /**
     * Start the viewer.
     *
     * @param rrd URLs to `.rrd` files or WebSocket connections to our SDK.
     * @param parent The element to attach the canvas onto.
     * @param options Whether to hide the welcome screen.
     */
    async start(rrd, parent, options) {
        parent ??= document.body;
        options ??= {};
        this.#allow_fullscreen = options.allow_fullscreen || false;
        if (this.#state !== "stopped")
            return;
        this.#state = "starting";
        this.#canvas = document.createElement("canvas");
        this.#canvas.style.width = options.width ?? "640px";
        this.#canvas.style.height = options.height ?? "360px";
        parent.append(this.#canvas);
        // This yield appears to be necessary to ensure that the canvas is attached to the DOM
        // and visible. Without it we get occasionally get a panic about a failure to find a canvas
        // element with the given ID.
        await delay(0);
        let WebHandle_class = await load();
        if (this.#state !== "starting")
            return;
        const fullscreen = this.#allow_fullscreen
            ? {
                get_state: () => this.#fullscreen,
                on_toggle: () => this.toggle_fullscreen(),
            }
            : undefined;
        const timeline = {
            on_timelinechange: (timeline, time) => this.#dispatch_event("timelinechange", timeline, time),
            on_timeupdate: (time) => this.#dispatch_event("timeupdate", time),
            on_pause: () => this.#dispatch_event("pause"),
            on_play: () => this.#dispatch_event("play"),
        };
        this.#handle = new WebHandle_class({ ...options, fullscreen, timeline });
        try {
            await this.#handle.start(this.#canvas);
        }
        catch (e) {
            this.stop();
            throw e;
        }
        if (this.#state !== "starting")
            return;
        this.#state = "ready";
        this.#dispatch_event("ready");
        if (rrd) {
            this.open(rrd);
        }
        return;
    }
    #event_map = new Map();
    #dispatch_event(event, ...args) {
        // Dispatch events on next tick.
        // This is necessary because we may have been called somewhere deep within the viewer's call stack,
        // which means that `app` may be locked. The event will not actually be dispatched until the
        // full call stack has returned or the current task has yielded to the event loop. It does not
        // guarantee that we will be able to acquire the lock here, but it makes it a lot more likely.
        setTimeout(() => {
            const callbacks = this.#event_map.get(event);
            if (callbacks) {
                for (const [callback, { once }] of [...callbacks.entries()]) {
                    callback(...args);
                    if (once)
                        callbacks.delete(callback);
                }
            }
        }, 0);
    }
    on(event, callback) {
        const callbacks = this.#event_map.get(event) ?? new Map();
        callbacks.set(callback, { once: false });
        this.#event_map.set(event, callbacks);
        return () => callbacks.delete(callback);
    }
    once(event, callback) {
        const callbacks = this.#event_map.get(event) ?? new Map();
        callbacks.set(callback, { once: true });
        this.#event_map.set(event, callbacks);
        return () => callbacks.delete(callback);
    }
    off(event, callback) {
        const callbacks = this.#event_map.get(event);
        if (callbacks) {
            callbacks.delete(callback);
        }
        else {
            console.warn("Attempted to call `WebViewer.off` with an unregistered callback. Are you passing in the same function instance?");
        }
    }
    /**
     * The underlying canvas element.
     */
    get canvas() {
        return this.#canvas;
    }
    /**
     * Returns `true` if the viewer is ready to connect to data sources.
     */
    get ready() {
        return this.#state === "ready";
    }
    /**
     * Open a recording.
     *
     * The viewer must have been started via `WebViewer.start`.
     *
     * @param rrd URLs to `.rrd` files or WebSocket connections to our SDK.
     * @param options
     *        - follow_if_http: Whether Rerun should open the resource in "Following" mode when streaming
     *        from an HTTP url. Defaults to `false`. Ignored for non-HTTP URLs.
     */
    open(rrd, options = {}) {
        if (!this.#handle) {
            throw new Error(`attempted to open \`${rrd}\` in a stopped viewer`);
        }
        const urls = Array.isArray(rrd) ? rrd : [rrd];
        for (const url of urls) {
            try {
                this.#handle.add_receiver(url, options.follow_if_http);
            }
            catch (e) {
                this.stop();
                throw e;
            }
        }
    }
    /**
     * Close a recording.
     *
     * The viewer must have been started via `WebViewer.start`.
     *
     * @param rrd URLs to `.rrd` files or WebSocket connections to our SDK.
     */
    close(rrd) {
        if (!this.#handle) {
            throw new Error(`attempted to close \`${rrd}\` in a stopped viewer`);
        }
        const urls = Array.isArray(rrd) ? rrd : [rrd];
        for (const url of urls) {
            try {
                this.#handle.remove_receiver(url);
            }
            catch (e) {
                this.stop();
                throw e;
            }
        }
    }
    /**
     * Stop the viewer, freeing all associated memory.
     *
     * The same viewer instance may be started multiple times.
     */
    stop() {
        if (this.#state === "stopped")
            return;
        if (this.#allow_fullscreen && this.#canvas && this.#fullscreen) {
            this.#minimize();
        }
        this.#state = "stopped";
        this.#canvas?.remove();
        try {
            this.#handle?.destroy();
            this.#handle?.free();
        }
        catch (e) {
            this.#handle = null;
            throw e;
        }
        this.#canvas = null;
        this.#handle = null;
        this.#fullscreen = false;
        this.#allow_fullscreen = false;
    }
    /**
     * Opens a new channel for sending log messages.
     *
     * The channel can be used to incrementally push `rrd` chunks into the viewer.
     *
     * @param channel_name used to identify the channel.
     */
    open_channel(channel_name = "rerun-io/web-viewer") {
        if (!this.#handle) {
            throw new Error(`attempted to open channel \"${channel_name}\" in a stopped web viewer`);
        }
        const id = crypto.randomUUID();
        try {
            this.#handle.open_channel(id, channel_name);
        }
        catch (e) {
            this.stop();
            throw e;
        }
        const on_send = (/** @type {Uint8Array} */ data) => {
            if (!this.#handle) {
                throw new Error(`attempted to send data through channel \"${channel_name}\" to a stopped web viewer`);
            }
            try {
                this.#handle.send_rrd_to_channel(id, data);
            }
            catch (e) {
                this.stop();
                throw e;
            }
        };
        const on_close = () => {
            if (!this.#handle) {
                throw new Error(`attempted to send data through channel \"${channel_name}\" to a stopped web viewer`);
            }
            try {
                this.#handle.close_channel(id);
            }
            catch (e) {
                this.stop();
                throw e;
            }
        };
        const get_state = () => this.#state;
        return new LogChannel(on_send, on_close, get_state);
    }
    /**
     * Force a panel to a specific state.
     *
     * @param panel
     * @param state
     */
    override_panel_state(panel, state) {
        if (!this.#handle) {
            throw new Error(`attempted to set ${panel} panel to ${state} in a stopped web viewer`);
        }
        try {
            this.#handle.override_panel_state(panel, state);
        }
        catch (e) {
            this.stop();
            throw e;
        }
    }
    /**
     * Toggle panel overrides set via `override_panel_state`.
     *
     * @param value - set to a specific value. Toggles the previous value if not provided.
     */
    toggle_panel_overrides(value) {
        if (!this.#handle) {
            throw new Error(`attempted to toggle panel overrides in a stopped web viewer`);
        }
        try {
            this.#handle.toggle_panel_overrides(value);
        }
        catch (e) {
            this.stop();
            throw e;
        }
    }
    /**
     * Get the active recording id.
     */
    get_active_recording_id() {
        if (!this.#handle) {
            throw new Error(`attempted to get active recording id in a stopped web viewer`);
        }
        return this.#handle.get_active_recording_id() ?? null;
    }
    /**
     * Set the active recording id.
     */
    set_active_recording_id(value) {
        if (!this.#handle) {
            throw new Error(`attempted to set active recording id to ${value} in a stopped web viewer`);
        }
        this.#handle.set_active_recording_id(value);
    }
    /**
     * Get the play state.
     *
     * This always returns `false` if the recording can't be found.
     */
    get_playing(recording_id) {
        if (!this.#handle) {
            throw new Error(`attempted to get play state in a stopped web viewer`);
        }
        return this.#handle.get_playing(recording_id) || false;
    }
    /**
     * Set the play state.
     *
     * This does nothing if the recording can't be found.
     */
    set_playing(recording_id, value) {
        if (!this.#handle) {
            throw new Error(`attempted to set play state to ${value ? "playing" : "paused"} in a stopped web viewer`);
        }
        this.#handle.set_playing(recording_id, value);
    }
    /**
     * Get the current time.
     *
     * The interpretation of time depends on what kind of timeline it is:
     *
     * - For time timelines, this is the time in nanoseconds.
     * - For sequence timelines, this is the sequence number.
     *
     * This always returns `0` if the recording or timeline can't be found.
     */
    get_current_time(recording_id, timeline) {
        if (!this.#handle) {
            throw new Error(`attempted to get current time in a stopped web viewer`);
        }
        return this.#handle.get_time_for_timeline(recording_id, timeline) || 0;
    }
    /**
     * Set the current time.
     *
     * Equivalent to clicking on the timeline in the time panel at the specified `time`.
     * The interpretation of `time` depends on what kind of timeline it is:
     *
     * - For time timelines, this is the time in nanoseconds.
     * - For sequence timelines, this is the sequence number.
     *
     * This does nothing if the recording or timeline can't be found.
     *
     * @param value
     */
    set_current_time(recording_id, timeline, time) {
        if (!this.#handle) {
            throw new Error(`attempted to set current time to ${time} in a stopped web viewer`);
        }
        this.#handle.set_time_for_timeline(recording_id, timeline, time);
    }
    /**
     * Get the active timeline.
     *
     * This always returns `null` if the recording can't be found.
     */
    get_active_timeline(recording_id) {
        if (!this.#handle) {
            throw new Error(`attempted to get active timeline in a stopped web viewer`);
        }
        return this.#handle.get_active_timeline(recording_id) ?? null;
    }
    /**
     * Set the active timeline.
     *
     * This does nothing if the recording or timeline can't be found.
     */
    set_active_timeline(recording_id, timeline) {
        if (!this.#handle) {
            throw new Error(`attempted to set active timeline to ${timeline} in a stopped web viewer`);
        }
        this.#handle.set_active_timeline(recording_id, timeline);
    }
    /**
     * Get the time range for a timeline.
     *
     * This always returns `null` if the recording or timeline can't be found.
     */
    get_time_range(recording_id, timeline) {
        if (!this.#handle) {
            throw new Error(`attempted to get time range in a stopped web viewer`);
        }
        return this.#handle.get_timeline_time_range(recording_id, timeline);
    }
    /**
     * Toggle fullscreen mode.
     *
     * This does nothing if `allow_fullscreen` was not set to `true` when starting the viewer.
     *
     * Fullscreen mode works by updating the underlying `<canvas>` element's `style`:
     * - `position` to `fixed`
     * - width/height/top/left to cover the entire viewport
     *
     * When fullscreen mode is toggled off, the style is restored to its previous values.
     *
     * When fullscreen mode is toggled on, any other instance of the viewer on the page
     * which is already in fullscreen mode is toggled off. This means that it doesn't
     * have to be tracked manually.
     *
     * This functionality can also be directly accessed in the viewer:
     * - The maximize/minimize top panel button
     * - The `Toggle fullscreen` UI command (accessible via the command palette, CTRL+P)
     */
    toggle_fullscreen() {
        if (!this.#allow_fullscreen)
            return;
        if (!this.#handle || !this.#canvas) {
            throw new Error(`attempted to toggle fullscreen mode in a stopped web viewer`);
        }
        if (this.#fullscreen) {
            this.#minimize();
        }
        else {
            this.#maximize();
        }
    }
    #minimize = () => { };
    #maximize = () => {
        _minimize_current_fullscreen_viewer?.();
        const canvas = this.#canvas;
        const rect = canvas.getBoundingClientRect();
        const sync_style_to_rect = () => {
            canvas.style.left = rect.left + "px";
            canvas.style.top = rect.top + "px";
            canvas.style.width = rect.width + "px";
            canvas.style.height = rect.height + "px";
        };
        const undo_style = () => canvas.removeAttribute("style");
        const transition = (callback) => setTimeout(() => requestAnimationFrame(callback), transition_delay_ms);
        canvas.classList.add(classes.fullscreen_base, classes.fullscreen_rect);
        sync_style_to_rect();
        requestAnimationFrame(() => {
            if (!this.#fullscreen)
                return;
            canvas.classList.add(classes.transition);
            transition(() => {
                if (!this.#fullscreen)
                    return;
                undo_style();
                document.body.classList.add(classes.hide_scrollbars);
                document.documentElement.classList.add(classes.hide_scrollbars);
                this.#dispatch_event("fullscreen", true);
            });
        });
        this.#minimize = () => {
            document.body.classList.remove(classes.hide_scrollbars);
            document.documentElement.classList.remove(classes.hide_scrollbars);
            sync_style_to_rect();
            canvas.classList.remove(classes.fullscreen_rect);
            transition(() => {
                if (this.#fullscreen)
                    return;
                undo_style();
                canvas.classList.remove(classes.fullscreen_base, classes.transition);
            });
            _minimize_current_fullscreen_viewer = null;
            this.#fullscreen = false;
            this.#dispatch_event("fullscreen", false);
        };
        _minimize_current_fullscreen_viewer = () => this.#minimize();
        this.#fullscreen = true;
    };
}
export class LogChannel {
    #on_send;
    #on_close;
    #get_state;
    #closed = false;
    /**
     * @param on_send
     * @param on_close
     * @param get_state
     */
    constructor(on_send, on_close, get_state) {
        this.#on_send = on_send;
        this.#on_close = on_close;
        this.#get_state = get_state;
    }
    get ready() {
        return !this.#closed && this.#get_state() === "ready";
    }
    /**
     * Send an `rrd` containing log messages to the viewer.
     *
     * Does nothing if `!this.ready`.
     *
     * @param rrd_bytes Is an rrd file stored in a byte array, received via some other side channel.
     */
    send_rrd(rrd_bytes) {
        if (!this.ready)
            return;
        this.#on_send(rrd_bytes);
    }
    /**
     * Close the channel.
     *
     * Does nothing if `!this.ready`.
     */
    close() {
        if (!this.ready)
            return;
        this.#on_close();
        this.#closed = true;
    }
}
const classes = {
    hide_scrollbars: "rerun-viewer-hide-scrollbars",
    fullscreen_base: "rerun-viewer-fullscreen-base",
    fullscreen_rect: "rerun-viewer-fullscreen-rect",
    transition: "rerun-viewer-transition",
};
const transition_delay_ms = 100;
const css = `
  html.${classes.hide_scrollbars},
  body.${classes.hide_scrollbars} {
    scrollbar-gutter: auto !important;
    overflow: hidden !important;
  }

  .${classes.fullscreen_base} {
    position: fixed;
    z-index: 99999;
  }

  .${classes.transition} {
    transition: all ${transition_delay_ms / 1000}s linear;
  }

  .${classes.fullscreen_rect} {
    left: 0;
    top: 0;
    width: 100%;
    height: 100%;
  }
`;
function injectStyle() {
    const ID = "__rerun_viewer_style";
    if (document.getElementById(ID)) {
        // already injected
        return;
    }
    const style = document.createElement("style");
    style.id = ID;
    style.appendChild(document.createTextNode(css));
    document.head.appendChild(style);
}
function setupGlobalEventListeners() {
    window.addEventListener("keyup", (e) => {
        if (e.code === "Escape") {
            _minimize_current_fullscreen_viewer?.();
        }
    });
}
//# sourceMappingURL=index.js.map