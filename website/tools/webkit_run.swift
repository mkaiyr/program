// Запуск страниц сайта в настоящем движке WebKit (WKWebView из macOS), сценарии на JavaScript и скриншоты. Только macOS, Xcode не нужен (хватает Command Line Tools).
// Используется tools/browser_tests.py:  webkit_run <план.json>
// План: {"width":390,"height":800,"phases":[{"url":"http://127.0.0.1:PORT/репозиторий/menu.html","waitFor":"#grid .dish-card","script":"return JSON.stringify({...})","screenshot":"/путь.png","full":true}, ...]}
// Все фазы выполняются в одном процессе с общим временным хранилищем (localStorage сохраняется между фазами, на диск ничего не пишется).
// Вывод: один JSON в stdout. Окно почти прозрачное (alpha 0.01), без реакции на мышь; пользователь его практически не видит.
import Cocoa
import WebKit

@MainActor final class Runner: NSObject, WKNavigationDelegate, WKScriptMessageHandler {
    let web: WKWebView
    let window: NSWindow
    var loadCont: CheckedContinuation<Void, Error>?
    var console: [String] = []
    var width: CGFloat, height: CGFloat

    init(width: CGFloat, height: CGFloat) {
        self.width = width; self.height = height
        let cfg = WKWebViewConfiguration()
        cfg.websiteDataStore = WKWebsiteDataStore.nonPersistent()
        let ucc = WKUserContentController()
        let js = """
        (function(){ const send=(t,m)=>{try{window.webkit.messageHandlers.log.postMessage({t:t,m:String(m)})}catch(e){}};
          window.addEventListener('error',e=>send('error',(e.message||'')+' @'+(e.filename||'').split('/').pop()+':'+(e.lineno||'')));
          window.addEventListener('unhandledrejection',e=>send('rejection',(e.reason&&e.reason.message)||e.reason));
          const ce=console.error; console.error=function(){send('console.error',Array.from(arguments).join(' ')); ce.apply(console,arguments)};
          const cw=console.warn; console.warn=function(){send('console.warn',Array.from(arguments).join(' ')); cw.apply(console,arguments)}; })();
        """
        ucc.addUserScript(WKUserScript(source: js, injectionTime: .atDocumentStart, forMainFrameOnly: false))
        cfg.userContentController = ucc
        web = WKWebView(frame: NSRect(x: 0, y: 0, width: width, height: height), configuration: cfg)
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: width, height: height), styleMask: [.borderless], backing: .buffered, defer: false)
        window.alphaValue = 0.01; window.ignoresMouseEvents = true; window.level = .normal; window.hasShadow = false   // «видимое» для WebKit (иначе анимации приостановлены), но практически прозрачное
        super.init()
        ucc.add(self, name: "log")
        web.navigationDelegate = self
        window.contentView = web
        window.orderFrontRegardless()
    }

    func userContentController(_ c: WKUserContentController, didReceive m: WKScriptMessage) {
        if let d = m.body as? [String: Any] { console.append("\(d["t"] ?? "")| \(d["m"] ?? "")") }
    }
    func webView(_ w: WKWebView, didFinish n: WKNavigation!) { loadCont?.resume(); loadCont = nil }
    func webView(_ w: WKWebView, didFail n: WKNavigation!, withError e: Error) { loadCont?.resume(throwing: e); loadCont = nil }
    func webView(_ w: WKWebView, didFailProvisionalNavigation n: WKNavigation!, withError e: Error) { loadCont?.resume(throwing: e); loadCont = nil }

    func load(_ url: URL) async throws {
        try await withCheckedThrowingContinuation { (c: CheckedContinuation<Void, Error>) in loadCont = c; web.load(URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData)) }
    }
    func eval(_ js: String) async -> Any? {
        await withCheckedContinuation { (c: CheckedContinuation<Any?, Never>) in web.evaluateJavaScript(js) { r, _ in c.resume(returning: r) } }
    }
    func waitFor(_ selector: String, timeout: Double) async -> Bool {
        let esc = selector.replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "'", with: "\\'")
        let end = Date().addingTimeInterval(timeout)
        while Date() < end {
            if let ok = await eval("!!document.querySelector('\(esc)')") as? Bool, ok { return true }
            try? await Task.sleep(nanoseconds: 120_000_000)
        }
        return false
    }
    func runScript(_ body: String) async -> (String?, String?) {
        await withCheckedContinuation { (c: CheckedContinuation<(String?, String?), Never>) in
            web.callAsyncJavaScript(body, arguments: [:], in: nil, in: .page) { res in
                switch res {
                case .success(let v): c.resume(returning: ((v as? String) ?? "\(v)", nil))
                case .failure(let e): c.resume(returning: (nil, "\(e)"))
                }
            }
        }
    }
    func snapshot(path: String, full: Bool) async -> String? {
        if full, let h = await eval("document.documentElement.scrollHeight") as? Double {
            let hh = min(max(CGFloat(h), height), 9000)
            window.setFrame(NSRect(x: 0, y: 0, width: width, height: hh), display: true); web.frame = NSRect(x: 0, y: 0, width: width, height: hh)
            try? await Task.sleep(nanoseconds: 500_000_000)
        }
                _ = await runScript("const vis = [...document.images].filter(i => i.loading !== 'lazy' || i.getBoundingClientRect().top < innerHeight + 50); await Promise.race([Promise.all(vis.map(i => i.complete ? null : new Promise(r => { i.addEventListener('load', r); i.addEventListener('error', r); }))), new Promise(r => setTimeout(r, 4000))]); await new Promise(r => setTimeout(r, 800)); return 'ok'")
        let cfg = WKSnapshotConfiguration(); cfg.snapshotWidth = NSNumber(value: Double(width))
        let img: NSImage? = await withCheckedContinuation { (c: CheckedContinuation<NSImage?, Never>) in web.takeSnapshot(with: cfg) { i, _ in c.resume(returning: i) } }
        guard let img = img, let tiff = img.tiffRepresentation, let rep = NSBitmapImageRep(data: tiff), let png = rep.representation(using: .png, properties: [:]) else { return "не удалось сделать снимок" }
        do { try png.write(to: URL(fileURLWithPath: path)); return nil } catch { return "\(error)" }
    }
}

func dbg(_ m: String) { if ProcessInfo.processInfo.environment["WK_DEBUG"] != nil { FileHandle.standardError.write((m + "\n").data(using: .utf8)!) } }

@MainActor func mainTask() async {
    guard CommandLine.arguments.count > 1, let data = FileManager.default.contents(atPath: CommandLine.arguments[1]),
          let plan = try? JSONSerialization.jsonObject(with: data) as? [String: Any], let phases = plan["phases"] as? [[String: Any]] else {
        print("{\"fatal\":\"нет плана\"}"); exit(2)
    }
    let w = CGFloat((plan["width"] as? Double) ?? 390), h = CGFloat((plan["height"] as? Double) ?? 800)
    let r = Runner(width: w, height: h)
    var out: [[String: Any]] = []
    for p in phases {
        var res: [String: Any] = [:]
        if let u = p["url"] as? String, let url = URL(string: u) {
            dbg("загрузка \(u)")
            do { try await r.load(url) } catch { res["error"] = "загрузка: \(error)"; out.append(res); continue }
        }
        dbg("загружено")
        if let sel = p["waitFor"] as? String {
            let ok = await r.waitFor(sel, timeout: (p["timeout"] as? Double) ?? 10)
            res["waited"] = ok
            if !ok { res["error"] = "не дождались элемента \(sel)" }
        }
        dbg("ожидание завершено")
        if let sc = p["script"] as? String {
            let (v, e) = await r.runScript(sc)
            if let v = v { res["result"] = v }; if let e = e { res["error"] = (res["error"] as? String ?? "") + " сценарий: \(e)" }
        }
        dbg("сценарий завершён")
        if let shot = p["screenshot"] as? String {
            if let e = await r.snapshot(path: shot, full: (p["full"] as? Bool) ?? false) { res["screenshot_error"] = e } else { res["screenshot"] = shot }
        }
        out.append(res)
    }
    let final: [String: Any] = ["phases": out, "console": r.console]
    if let d = try? JSONSerialization.data(withJSONObject: final), let s = String(data: d, encoding: .utf8) { print(s) }
    exit(0)
}

NSApplication.shared.setActivationPolicy(.accessory)
Task { @MainActor in await mainTask() }
NSApplication.shared.run()
