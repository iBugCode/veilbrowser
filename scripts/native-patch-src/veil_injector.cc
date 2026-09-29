// Copyright 2026 veilbrowser contributors
// Use of this source code is governed by the MIT license found in the
// repository LICENSE (Chromium upstream remains BSD-style).

#include "chrome/renderer/veil/veil_injector.h"

#include <string>
#include <utility>

#include "base/environment.h"
#include "base/logging.h"
#include "base/no_destructor.h"
#include "chrome/renderer/veil/veil_bundle.h"
#include "content/public/renderer/render_frame.h"
#include "third_party/blink/public/platform/scheduler/web_agent_group_scheduler.h"
#include "third_party/blink/public/web/web_local_frame.h"

namespace veil {

namespace {

constexpr char kVeilParamsEnv[] = "VEIL_PARAMS";

std::string VeilParamsJson() {
  if (auto val = base::Environment::Create()->GetVar(kVeilParamsEnv))
    return std::move(*val);
  return {};
}

void ReplaceToken(std::string* text,
                  const std::string& token,
                  const std::string& value) {
  const size_t pos = text->find(token);
  if (pos != std::string::npos)
    text->replace(pos, token.size(), value);
}

// Composes the final bundle once per renderer process: the metric-clone font
// payloads are compiled into the binary, the profile parameters arrive via
// VEIL_PARAMS (small JSON), and the bundle template's single __VEIL_CFG__
// slot resolves through veilNativeCfg() defined in the prelude.
const std::string& ComposedScript() {
  static const base::NoDestructor<std::string> script([] {
    std::string json = VeilParamsJson();
    if (json.empty())
      json = "null";
    std::string s;
    s.reserve(1024 * 1024);
    s += "var __veilFontData=";
    s += kVeilFontData;
    s += ";\n";
    s += kVeilScriptTemplate;
    ReplaceToken(&s, "__VEIL_CFG__", "veilNativeCfg(__VEIL_PARAMS_JSON__)");
    ReplaceToken(&s, "__VEIL_PARAMS_JSON__", json);
    return s;
  }());
  return *script;
}

void RunVeilBundle(v8::Isolate* isolate, v8::Local<v8::Context> context) {
  v8::HandleScope handle_scope(isolate);
  v8::Context::Scope context_scope(context);

  const std::string& source = ComposedScript();
  v8::Local<v8::String> text =
      v8::String::NewFromUtf8(isolate, source.data(),
                              v8::NewStringType::kNormal,
                              static_cast<int>(source.size()))
          .ToLocalChecked();
  if (text.IsEmpty())
    return;
  v8::TryCatch try_catch(isolate);
  v8::Local<v8::Script> compiled;
  if (!v8::Script::Compile(context, text).ToLocal(&compiled)) {
    v8::Local<v8::Value> exc = try_catch.HasCaught()
                                   ? try_catch.Exception()
                                   : v8::Local<v8::Value>();
    if (!exc.IsEmpty()) {
      v8::String::Utf8Value msg(isolate, exc);
      v8::Local<v8::Message> m = try_catch.Message();
      int line = m.IsEmpty() ? -1
                             : m->GetLineNumber(context).FromMaybe(-1);
      LOG(WARNING) << "veil: fingerprint bundle compile failed: " << *msg
                   << " (line " << line << ")";
    } else {
      LOG(WARNING) << "veil: fingerprint bundle compile returned empty";
    }
    return;
  }
  v8::Local<v8::Value> result;
  if (!compiled->Run(context).ToLocal(&result))
    LOG(WARNING) << "veil: fingerprint bundle threw";
}

}  // namespace

VeilFrameInjector::VeilFrameInjector(content::RenderFrame* frame)
    : content::RenderFrameObserver(frame) {}

void VeilFrameInjector::DidCreateScriptContext(
    v8::Local<v8::Context> context,
    int world_id) {
  // Main world only: isolated worlds (extensions) already observe the patched
  // main-world APIs through the DOM.
  if (world_id != 0)
    return;
  RunVeilBundle(
      render_frame()->GetWebFrame()->GetAgentGroupScheduler()->Isolate(),
      context);
}

void VeilFrameInjector::OnDestruct() {
  delete this;
}

void RunVeilBundleInContext(v8::Local<v8::Context> context) {
  // On the worker thread the freshly created context's isolate is the
  // current one.
  RunVeilBundle(v8::Isolate::GetCurrent(), context);
}

}  // namespace veil
