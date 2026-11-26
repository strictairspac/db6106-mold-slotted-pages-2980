export const States = Object.freeze({
  queued: "等待执行", done: "已完成", failed: "执行失败",
  open: "取舍中", handed: "已交班",
});

export const Actions = Object.freeze({
  create_case: "登记资料", enqueue: "提交执行", done: "执行完成", failed: "执行失败", review: "复核签署",
  create_merge: "登记合单", choices: "提交取舍", handover: "交班签署",
});
