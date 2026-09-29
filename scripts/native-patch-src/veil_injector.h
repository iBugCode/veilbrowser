// Copyright 2026 veilbrowser contributors
// Use of this source code is governed by the MIT license found in the
// repository LICENSE (Chromium upstream remains BSD-style).
//
// veilbrowser native fingerprint engine: the compiled-in veil bundle runs at
// document-start in every main-world script context and in every worker
// context, so the browser carries its full fingerprint with no external
// injection — no CDP addScriptToEvaluateOnNewDocument, no extension, no
// page-visible wrapper bootstrap.
//
// Profile parameters arrive via the VEIL_PARAMS environment variable (small
// JSON produced by veilbrowser.inject.js_params minus fontData); the
// metric-clone font payloads ship inside the binary.

#ifndef CHROME_RENDERER_VEIL_VEIL_INJECTOR_H_
#define CHROME_RENDERER_VEIL_VEIL_INJECTOR_H_

#include "content/public/renderer/render_frame_observer.h"
#include "v8/include/v8.h"

namespace veil {

class VeilFrameInjector : public content::RenderFrameObserver {
 public:
  explicit VeilFrameInjector(content::RenderFrame* frame);

  // content::RenderFrameObserver:
  void DidCreateScriptContext(v8::Local<v8::Context> context,
                              int world_id) override;
  void OnDestruct() override;
};

// Runs the bundle into an arbitrary context (worker threads included).
void RunVeilBundleInContext(v8::Local<v8::Context> context);

}  // namespace veil

#endif  // CHROME_RENDERER_VEIL_VEIL_INJECTOR_H_
