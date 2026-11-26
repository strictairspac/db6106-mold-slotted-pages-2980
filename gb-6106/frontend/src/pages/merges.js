import { api } from "../api/workbench.js";
import { errorText, field } from "../components/common/fields.js";
import { empty, pageHeading, section } from "../components/common/panel.js";
import { status } from "../components/common/status.js";
import { resultTable, table } from "../components/common/table.js";
import { poll } from "../hooks/poll.js";
import { submit } from "../hooks/submit.js";
import { navigate } from "../router/router.js";
import { el } from "../utils/dom.js";
import { cell, download, time } from "../utils/format.js";

const SIDES = {a: "白班", b: "夜班"};

function createForm(done) {
  if (!done.length) return empty("还没有已完工的变更单批次，先在槽页重放里跑完两张单。");
  const options = () => done.map(run => el("option", {value: run.id},
    `#${run.id} ${run.case_title} · ${run.parameter} 字节`));
  const form = el("form", {},
    field("合单名称", el("input", {name: "title", required: true, minlength: 2, maxlength: 120})),
    field("白班变更单（已完工）", el("select", {name: "run_a_id", required: true}, options())),
    field("夜班变更单（已完工）", el("select", {name: "run_b_id", required: true}, options())),
    errorText(), el("button", {type: "submit"}, "登记交班合单"));
  submit(form, async values => {
    const merge = await api.createMerge({
      title: values.get("title"),
      run_a_id: Number(values.get("run_a_id")),
      run_b_id: Number(values.get("run_b_id")),
    });
    navigate(`merges/${merge.id}`);
  });
  return form;
}

function sourceCard(label, source) {
  return el("div", {class: "source-card"},
    el("h3", {}, `${label} · 批次 #${source.id}`),
    el("p", {}, source.case_title),
    el("p", {}, `${time(source.finished_at)} 完工 · ${source.parameter} 字节`),
    el("a", {href: `#evidence/${source.id}`}, "原凭据照常查 →"));
}

export async function mergesPage(config, user, route, signal) {
  const merges = await api.merges();
  const selected = merges.find(item => item.id === route.id) ?? merges.at(-1);
  const canDecide = user.role === "operator" || user.role === "reviewer";
  const done = user.role === "operator" ? (await api.runs()).filter(run => run.status === "done") : [];
  const container = el("div", {class: "stack"});
  let runId = null;
  let polling = false;

  function choicesForm(merge) {
    const form = el("form", {class: "choices-form"});
    for (const key of merge.conflicts) {
      const current = merge.choices?.[key];
      form.append(el("div", {class: "field"},
        el("span", {}, `履历 ${key}`),
        el("label", {class: "choice"}, el("input", {
          type: "radio", name: `key-${key}`, value: "a", required: true, checked: current === "a",
        }), "白班"),
        el("label", {class: "choice"}, el("input", {
          type: "radio", name: `key-${key}`, value: "b", required: true, checked: current === "b",
        }), "夜班")));
    }
    form.append(errorText(), el("button", {type: "submit"}, "确认取舍"));
    submit(form, async values => {
      const choices = {};
      for (const key of merge.conflicts) choices[key] = values.get(`key-${key}`);
      await api.submitChoices(merge.id, {choices});
      await refresh();
    });
    return form;
  }

  function overviewBlock(merge) {
    return el("div", {class: "stack"},
      el("p", {}, status(merge.status),
        ` 页容量 ${merge.parameter} 字节 · 取舍版本 v${merge.choices_version}`),
      merge.choices_by ?
        el("p", {}, `最近取舍由 ${merge.choices_by} 于 ${time(merge.choices_at)} 确认；两人同时提交时，以最后一次确认为准。`) :
        el("p", {}, "还没有提交取舍。"),
      el("div", {class: "split"}, sourceCard("白班", merge.run_a), sourceCard("夜班", merge.run_b)));
  }

  function choicesSection(merge) {
    const children = [];
    if (!merge.conflicts.length) {
      children.push(empty("两张单没有动过同一履历，无需取舍，可直接合单。"));
    } else {
      children.push(table(["履历", "当前取舍"], merge.conflicts.map(key =>
        [key, merge.choices?.[key] ? SIDES[merge.choices[key]] : "待定"]), "冲突履历"));
      if (merge.status === "open" && canDecide) children.push(choicesForm(merge));
      else if (!merge.choices) children.push(empty("待操作员或复核员提交取舍。"));
    }
    return section("冲突履历取舍", ...children);
  }

  function enqueueForm(merge) {
    const missing = merge.conflicts.filter(key => !merge.choices?.[key]);
    const form = el("form", {class: "queue-form"}, errorText(),
      el("button", {type: "submit", disabled: missing.length > 0 || null}, "提交合单执行"));
    submit(form, async () => {
      await api.enqueueMerge(merge.id);
      await refresh();
      ensurePolling();
    });
    return el("div", {},
      missing.length ? el("p", {class: "feedback"}, `还有履历未取舍：${missing.join("、")}`) : null, form);
  }

  function runDetailNodes(run) {
    const nodes = [];
    if (run.stale) nodes.push(el("p", {class: "feedback"}, "该批次按旧取舍跑完，不能用于交班。"));
    if (run.status === "done") {
      const result = run.result;
      nodes.push(el("p", {}, result.summary),
        el("dl", {class: "metrics"}, Object.entries(result.metrics).map(([label, value]) =>
          el("div", {}, el("dt", {}, label), el("dd", {}, cell(value))))));
      if (result.conflicts?.length) nodes.push(el("h3", {}, "冲突取舍"), resultTable(result.conflicts));
      if (!result.fitted) nodes.push(el("h3", {}, "卡住的履历"), resultTable(result.stuck));
      if (result.rows?.length) nodes.push(el("h3", {}, "重放明细"), resultTable(result.rows));
    } else if (run.status === "failed") {
      nodes.push(el("p", {class: "feedback"}, run.failure));
    } else {
      nodes.push(el("p", {}, "后台正在处理，页面会自动更新。"));
    }
    return nodes;
  }

  async function runsSection(merge) {
    const children = [];
    if (merge.status === "open" && user.role === "operator") children.push(enqueueForm(merge));
    if (!merge.runs.length) {
      children.push(empty("还没有合单批次，提交取舍后即可执行。"));
      return section("合单批次", ...children);
    }
    children.push(table(["编号", "取舍版本", "状态", "结果", "提交时间", "证据"], merge.runs.map(run => [
      `#${run.id}`,
      `v${run.choices_version}${run.stale ? "（旧取舍）" : ""}`,
      status(run.status),
      run.status === "done" ? (run.fitted ? "全写入" : "装不下") : "—",
      time(run.created_at),
      run.status === "done" ? el("button", {class: "quiet", type: "button", onclick: async event => {
        const button = event.currentTarget;
        button.disabled = true;
        try {
          await download(await api.exportMergeRun(merge.id, run.id), `handover-${merge.id}-${run.id}.json`);
        } finally {
          button.disabled = false;
        }
      }}, "下载") : "—",
    ]), "合单批次"));
    const runBox = el("div", {});
    async function renderRunDetail() {
      if (!runId) return;
      const run = await api.mergeRun(merge.id, runId, signal);
      if (!signal.aborted) runBox.replaceChildren(...runDetailNodes(run));
    }
    children.push(field("查看批次明细", el("select", {"aria-label": "选择合单批次", onchange: event => {
      runId = Number(event.target.value);
      renderRunDetail();
    }}, merge.runs.map(run => el("option", {value: run.id, selected: run.id === runId},
      `#${run.id} · 取舍 v${run.choices_version}${run.stale ? " · 旧取舍" : ""}`)))), runBox);
    await renderRunDetail();
    return section("合单批次", ...children);
  }

  function handoverSection(merge) {
    if (merge.handover) {
      return section("交班签署", el("div", {class: "signature"},
        el("strong", {}, `已交班 · ${merge.handover.actor}`),
        el("p", {}, merge.handover.note),
        el("p", {}, `交班批次 #${merge.handover.run_id}`),
        el("time", {}, time(merge.handover.created_at))));
    }
    if (user.role !== "reviewer") return section("交班签署", empty("待复核员签署交班。"));
    const eligible = merge.runs.filter(run => run.status === "done" && run.fitted && !run.stale);
    if (!eligible.length) return section("交班签署", empty("需要最新取舍跑完且全部写入的批次才能交班。"));
    const form = el("form", {},
      field("交班批次", el("select", {name: "run_id"}, eligible.map(run =>
        el("option", {value: run.id}, `#${run.id} · 取舍 v${run.choices_version}`)))),
      field("交班备注", el("textarea", {name: "note", required: true, minlength: 3, maxlength: 500, rows: 3})),
      errorText(), el("button", {type: "submit"}, "签署交班"));
    submit(form, async values => {
      await api.handover(merge.id, {run_id: Number(values.get("run_id")), note: values.get("note")});
      await refresh();
    });
    return section("交班签署", form);
  }

  async function renderDetail() {
    const merge = await api.merge(selected.id, signal);
    if (signal.aborted) return null;
    if (!merge.runs.some(run => run.id === runId)) runId = merge.runs[0]?.id ?? null;
    container.replaceChildren(
      section(`合单概况 · ${merge.title}`, overviewBlock(merge)),
      choicesSection(merge),
      await runsSection(merge),
      handoverSection(merge));
    return merge;
  }

  async function refresh() {
    const merge = await renderDetail();
    return !!merge?.runs.some(run => run.status === "queued");
  }

  function ensurePolling() {
    if (polling) return;
    polling = true;
    poll(async () => {
      const waiting = await refresh();
      if (!waiting) polling = false;
      return waiting;
    }, signal);
  }

  const sections = [
    pageHeading(config.modules[4], "白班与夜班各一张已完工变更单，交错重放合成一张交班单。"),
    section("合单目录", merges.length ? table(["编号", "名称", "状态", "取舍版本", "登记时间"], merges.map(item => [
      item.code, item.title, status(item.status), `v${item.choices_version}`, time(item.created_at),
      el("a", {href: `#merges/${item.id}`, "aria-current": item.id === selected?.id ? "true" : null}, "选用"),
    ]), "合单目录") : empty("这里还没有交班合单。")),
  ];
  if (user.role === "operator") sections.push(section("登记交班合单", createForm(done)));
  if (selected) {
    sections.push(container);
    if (await refresh()) ensurePolling();
  } else {
    sections.push(empty("先登记一张交班合单。"));
  }
  return el("div", {class: "page"}, ...sections);
}
