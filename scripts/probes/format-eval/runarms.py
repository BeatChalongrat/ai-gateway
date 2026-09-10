import json, os, subprocess, sys, time
SP=sys.argv[1]; MODEL=sys.argv[2]; RUNS=int(sys.argv[3])
KEY=open(os.path.join(SP,'key.txt'),encoding='utf-8').read().strip()
pre=json.load(open(os.path.join(SP,'prereg.json'),encoding='utf-8'))
ARMS={a: open(os.path.join(SP,f'arm-{a}.txt'),encoding='utf-8').read() for a in ('before','after')}
out=open(os.path.join(SP,f'arms-{MODEL.split("-")[0]}.jsonl'),'a',encoding='utf-8')

def call(system, q, timeout=240):
    body={"model":MODEL,"max_tokens":2048,"stream":True,
          "messages":[{"role":"system","content":system},{"role":"user","content":q}]}
    p=os.path.join(SP,'_a.json'); json.dump(body,open(p,'w',encoding='utf-8'),ensure_ascii=False)
    pr=subprocess.Popen(["curl","-sS","-N","-m",str(timeout),"-X","POST",
        "http://localhost:8080/v1/chat/completions","-H","Authorization: Bearer "+KEY,
        "-H","Content-Type: application/json","-d","@"+p],
        stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,text=True,encoding='utf-8',errors='replace')
    text=[]; err=None; cached=None
    for line in pr.stdout:
        line=line.strip()
        if not line.startswith("data:"): continue
        pay=line[5:].strip()
        if pay=="[DONE]": continue
        try: d=json.loads(pay)
        except Exception: continue
        if d.get("cached") is not None: cached=d["cached"]
        if d.get("error"): err=d["error"].get("code"); continue
        t=d.get("type")
        if t in ("content_delta","text_delta","delta") and (d.get("text") or d.get("content")):
            text.append(d.get("text") or d.get("content"))
        elif "choices" in d:
            ch=(d["choices"] or [{}])[0]
            s=(ch.get("delta") or {}).get("content") or ch.get("text")
            if s: text.append(s)
    pr.wait()
    return "".join(text), err, cached

for run in range(1, RUNS+1):
    for ask in pre["asks"]:
        for arm in ("before","after"):
            # never repeat (model, messages): the question differs per ask and the
            # system prompt differs per arm, so no two calls share a cache key.
            txt, err, cached = call(ARMS[arm], ask["text"])
            rec={"model":MODEL,"arm":arm,"id":ask["id"],"lang":ask["lang"],"label":ask["label"],
                 "run":run,"err":err,"cached":cached,"text":txt}
            out.write(json.dumps(rec,ensure_ascii=False)+"\n"); out.flush()
            print(f"run{run} {ask['id']} {arm:6s} err={err} chars={len(txt)}", flush=True)
out.close(); print("ARMS-DONE", flush=True)
