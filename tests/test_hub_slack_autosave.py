"""GitHub issue #2908: the Slack settings save by themselves (SPEC 2682).

The owner found the save machinery spread over the tree: a Save bar, a "Save the writes" button, a "not saved" chip and
a "Proposed writes" table. Each edit now saves by itself. An edit of the local file saves at once; an edit of the
committed file saves as one commit two seconds after the last edit of a burst, or when the owner leaves the node. The
bar keeps one status line, with Undo after a save and Retry after a refusal.

The seams under test are the page's own functions, run in node over stubbed timers and a stubbed fetch, and the client
route of the hub.
"""
import json
import pathlib
import re

import yaml

from tests import test_hub_slack_clients as C

FOLLOWER, _node, _slices, stub_write, wire = C.FOLLOWER, C._node, C._slices, C.stub_write, C.wire
hub = C.hub   # the shared module fixture, registered here under its own name

TIMERS = ('let TQ=[],TN=0;globalThis.setTimeout=(f,ms)=>{const id=++TN;TQ.push({id,f,ms});return id};'
          'globalThis.clearTimeout=id=>{TQ=TQ.filter(t=>t.id!==id)};'
          'const fire=()=>{const q=TQ;TQ=[];for(const t of q)t.f()};')
DOCS = ('slDocs={committed:{version:1,project:{language:"en-US"}},local:{version:1}};slSavedDocs=slClone(slDocs);'
        'slR={tree:{clients:[]}};slS=slR;slRender=()=>{};')   # the pane's markup is not under test here


def _post_src(page):
    return page[page.index("async function slPost("):page.index('$("#sbody").addEventListener("change"')]


def _run(hub, script, post=False):
    pre = TIMERS + ("let POSTS=[];" if not post else "")
    body = _slices(hub.PAGE) + "\n" + pre + (_post_src(hub.PAGE) if post else
                                             "slPost=async c=>{POSTS.push(JSON.stringify(slDocs))};") + "\n" + DOCS + script
    return json.loads(_node(body))


def test_a_local_edit_saves_at_once_and_a_committed_edit_waits_two_seconds(hub):
    got = _run(hub, 'slDocs.local.poll={fast:9};slAutoSchedule();const a=TQ.map(t=>t.ms);TQ=[];'
                    'slDocs.committed.project.language="pt-BR";slAutoSchedule();const b=TQ.map(t=>t.ms);'
                    'console.log(JSON.stringify({a,b}))')
    assert got["a"] == [300], got
    assert got["b"] == [2000], got


def test_a_burst_of_edits_saves_once(hub):
    got = _run(hub, 'for(const l of ["pt-BR","es-ES","en-GB"]){slDocs.committed.project.language=l;slAutoSchedule()}'
                    '(async()=>{const n=TQ.length;fire();await new Promise(r=>process.nextTick(r));'
                    'console.log(JSON.stringify({n,posts:POSTS.length,last:JSON.parse(POSTS[0]).committed.project.language}))})()')
    assert got["n"] == 1, got
    assert got["posts"] == 1 and got["last"] == "en-GB", got


def test_a_render_with_no_new_edit_does_not_push_the_save_back(hub):
    got = _run(hub, 'slDocs.committed.project.language="pt-BR";slAutoSchedule();const id=TQ[0].id;slAutoSchedule();'
                    'console.log(JSON.stringify({n:TQ.length,same:TQ[0].id===id}))')
    assert got == {"n": 1, "same": True}, got


def test_leaving_the_node_flushes_the_pending_save(hub):
    got = _run(hub, 'slDocs.committed.project.language="pt-BR";slAutoSchedule();slTPick("x");'
                    '(async()=>{await new Promise(r=>process.nextTick(r));console.log(JSON.stringify({posts:POSTS.length}))})()')
    assert got["posts"] == 1, got


def test_an_edit_made_during_a_save_is_kept_and_saves_after_it(hub):
    script = ('let SENT=[];let release;globalThis.fetch=(url,o)=>{if(o&&o.method==="POST"){SENT.push(JSON.parse(o.body));'
              'return new Promise(r=>{release=()=>r({json:()=>Promise.resolve({sha:"abc1234"})})})}'
              'return Promise.resolve({json:()=>Promise.resolve({docs:SENT[SENT.length-1],tree:{clients:[]}})})};'
              '(async()=>{slDocs.committed.project.language="pt-BR";slAutoSchedule();fire();'
              'await new Promise(r=>setImmediate(r));slDocs.committed.project.language="es-ES";slAutoSchedule();'
              'release();for(let i=0;i<5;i++)await new Promise(r=>setImmediate(r));const kept=slDocs.committed.project.language;'
              'fire();for(let i=0;i<5;i++)await new Promise(r=>setImmediate(r));release&&release();'
              'for(let i=0;i<5;i++)await new Promise(r=>setImmediate(r));'
              'console.log(JSON.stringify({kept,sent:SENT.map(s=>s.committed.project.language)}))})()')
    got = _run(hub, script, post=True)
    assert got["kept"] == "es-ES", got
    assert got["sent"] == ["pt-BR", "es-ES"], got


def test_the_bar_says_saved_and_offers_undo_and_a_refusal_offers_retry(hub):
    script = ('let OK=true;globalThis.fetch=(url,o)=>Promise.resolve({json:()=>Promise.resolve(o&&o.method==="POST"'
              '?(OK?{sha:"abc1234"}:{err:"a committed send_grant is invalid"}):{docs:JSON.parse(o?o.body:JSON.stringify(slDocs)),tree:{clients:[]}})});'
              '(async()=>{slDocs.local.poll={fast:9};slAutoSchedule();fire();for(let i=0;i<5;i++)await new Promise(r=>setImmediate(r));'
              'const a=slBarState();OK=false;slDocs.local.poll={fast:8};slAutoSchedule();fire();'
              'for(let i=0;i<5;i++)await new Promise(r=>setImmediate(r));const b=slBarState();'
              'console.log(JSON.stringify({a,b}))})()')
    got = _run(hub, script, post=True)
    assert got["a"]["text"].startswith("Saved"), got
    assert got["a"]["undo"] is True and got["a"]["retry"] is False and got["a"]["err"] is False, got
    assert "a committed send_grant is invalid" in got["b"]["text"], got
    assert got["b"]["retry"] is True and got["b"]["undo"] is False and got["b"]["err"] is True, got


def test_undo_puts_back_the_files_before_the_last_save(hub):
    script = ('globalThis.fetch=(url,o)=>Promise.resolve({json:()=>Promise.resolve(o&&o.method==="POST"?{sha:"abc1234"}'
              ':{docs:LAST,tree:{clients:[]}})});let LAST=null;const _f=globalThis.fetch;'
              'globalThis.fetch=(url,o)=>{if(o&&o.method==="POST")LAST=JSON.parse(o.body);return _f(url,o)};'
              '(async()=>{slDocs.committed.project.language="pt-BR";slAutoSchedule();fire();'
              'for(let i=0;i<5;i++)await new Promise(r=>setImmediate(r));slUndoLast();'
              'for(let i=0;i<5;i++)await new Promise(r=>setImmediate(r));'
              'console.log(JSON.stringify({lang:slDocs.committed.project.language,sent:LAST.committed.project.language}))})()')
    got = _run(hub, script, post=True)
    assert got == {"lang": "en-US", "sent": "en-US"}, got


def test_the_slack_theme_hides_save_and_discard(hub):
    page = hub.PAGE
    src = page[page.index("const STH="):page.index("function setTheme(")]
    stub = ('const STORE={};const $=s=>STORE[s]||(STORE[s]={innerHTML:"",textContent:"",disabled:false,hidden:null,'
            'classList:{toggle(){}}});const ls={};const mdlChanged=()=>false;const slDirty=()=>true;'
            'const slBarState=()=>({text:"Saved · landed abc1234",undo:true,retry:false,err:false});'
            'let RO=false;const RO_WHY="";')
    body = ('sTheme="slk";setBar();console.log(JSON.stringify({save:$("#ssave").hidden,disc:$("#sdisc").hidden,'
            'text:$("#schg").textContent,undo:$("#sundo").hidden}))')
    got = json.loads(_node("\n".join([stub, src, body])))
    assert got["save"] is True and got["disc"] is True, got
    assert got["text"] == "Saved · landed abc1234", got
    assert got["undo"] is False, got


def test_the_page_asks_before_it_closes_over_a_pending_edit(hub):
    assert re.search(r'addEventListener\("beforeunload"', hub.PAGE)


def test_the_client_node_shows_no_save_machinery_and_names_a_refused_project(hub, tmp_path, monkeypatch):
    wire(tmp_path, monkeypatch, hub,
         [{"id": "one", "name": "One", "default_persona": "blue", "projects": ["alpha"]}], {"alpha": FOLLOWER})
    out = hub.slack_overview()
    node = out["tree"]["clients"][0]
    node["proposal"] = [{"project": "alpha", "label": "alpha", "now": "Blue", "action": "write", "result": "writes Amber"},
                        {"project": "gamma", "label": "gamma", "now": "none", "action": "refused",
                         "result": "refused: Amber is not declared in this project"}]
    body = (_slices(hub.PAGE) + f"\nslS={{}};slR={json.dumps(out)};slTsel=\"c0\";slRender();"
            "console.log(JSON.stringify(STORE['#sbody'].innerHTML))")
    html = json.loads(_node(body))
    for gone in ("Save the writes", "Proposed writes", "not saved", "data-slt-write"):
        assert gone not in html, gone
    assert "gamma" in html and "Amber is not declared in this project" in html, html[-800:]


def test_a_client_default_change_saves_its_writes_by_itself(hub, tmp_path, monkeypatch):
    wire(tmp_path, monkeypatch, hub,
         [{"id": "one", "name": "One", "default_persona": "blue", "projects": ["alpha"]}], {"alpha": FOLLOWER})
    out = hub.slack_overview()
    body = (_slices(hub.PAGE) + "\n" + TIMERS + f"slS={{}};slR={json.dumps(out)};slTsel=\"c0\";"
            'let SENT=[];globalThis.fetch=(u,o)=>{SENT.push(JSON.parse(o.body));return Promise.resolve({status:200,'
            'json:()=>Promise.resolve({rows:[],proposal_saved:true,partial:false,refused:[]})})};'
            'const EL={value:"amber",options:[],selectedIndex:0};'
            'globalThis.document={getElementById:id=>id==="slclpersona"?EL:null};slRender();SENT=[];'
            '(async()=>{slClientPick();const n=TQ.length;fire();await new Promise(r=>setImmediate(r));'
            'console.log(JSON.stringify({n,sent:SENT}))})()')
    got = json.loads(_node(body))
    assert got["n"] == 1, got
    assert len(got["sent"]) == 1 and got["sent"][0].get("confirm") is True, got
    assert got["sent"][0]["persona"] == "amber", got


def test_a_client_default_save_records_the_default_in_the_client_map(hub, tmp_path, monkeypatch):
    prof, _ = wire(tmp_path, monkeypatch, hub,
                   [{"id": "one", "name": "One", "default_persona": {"id": "blue", "name": "Blue"},
                     "projects": [{"id": "alpha", "follows": True}]}], {"alpha": FOLLOWER})
    stub_write(hub, monkeypatch)
    out = hub.slack_clients_save({"client": "one", "persona": "amber", "persona_name": "Amber", "confirm": True})
    assert out.get("err") is None, out
    doc = yaml.safe_load((prof / "clients.local.yaml").read_text())
    assert doc["clients"][0]["default_persona"] == {"id": "amber", "name": "Amber"}, doc
    assert doc["clients"][0]["projects"] == [{"id": "alpha", "follows": True}], doc
    assert pathlib.Path(prof / "clients.local.yaml").exists()


def test_a_staged_edit_keeps_the_whole_tree_and_the_project_in_its_place(hub, tmp_path, monkeypatch):
    """A staged edit answers the tree of every project, with the edited project in its client and under its own node id,
    so the owner's selection never jumps to another node while the save waits (#2908)."""
    prof, made = wire(tmp_path, monkeypatch, hub,
                      [{"id": "one", "name": "One", "default_persona": "blue", "projects": ["alpha", "beta"]}],
                      {"alpha": FOLLOWER, "beta": FOLLOWER})
    co, committed, local = made["alpha"]
    monkeypatch.setattr(hub, "slack_yaml_paths", lambda: (str(committed), str(local)))
    before = hub.slack_overview()
    op = {"kind": "set", "doc": "committed", "path": ["workspaces", 0, "label"], "value": "renamed"}
    out = hub.slack_docs_edit({"op": op}, committed=str(committed), local=str(local))
    assert out.get("err") is None, out
    tree = out["tree"]
    assert [c["name"] for c in tree["clients"]] == [c["name"] for c in before["tree"]["clients"]], tree["clients"]
    assert tree["clients"][0]["name"] == "One", tree["clients"][0]["name"]
    names = [p["name"] for p in tree["clients"][0]["children"]]
    assert names == ["alpha", "beta"], names
    alpha = tree["clients"][0]["children"][0]
    assert alpha["children"][0]["name"] == "renamed", alpha["children"][0]["name"]
    assert alpha["id"] == "c0.p0", alpha["id"]
    assert tree["counts"] == before["tree"]["counts"], (tree["counts"], before["tree"]["counts"])
