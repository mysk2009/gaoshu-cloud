(() => {
  "use strict";

  const ERROR_LABEL = {
    concept: "概念错",
    calc: "计算错",
    careless: "看错",
    method: "方法错",
    none: "未标注",
  };

  const state = {
    token: sessionStorage.getItem("teacherToken") || "",
    board: null,
    showAllStudents: false,
  };

  const elements = {
    loginForm: document.getElementById("login-form"),
    classForm: document.getElementById("class-form"),
    password: document.getElementById("password"),
    classCode: document.getElementById("class-code"),
    logout: document.getElementById("logout"),
    notice: document.getElementById("notice"),
    submitted: document.getElementById("submitted"),
    roster: document.getElementById("roster"),
    missingCount: document.getElementById("missing-count"),
    attempts: document.getElementById("attempts"),
    missingEmpty: document.getElementById("missing-empty"),
    missingList: document.getElementById("missing-list"),
    copyMissing: document.getElementById("copy-missing"),
    studentsEmpty: document.getElementById("students-empty"),
    studentsWrap: document.getElementById("students-wrap"),
    studentsBody: document.getElementById("students-body"),
    toggleStudents: document.getElementById("toggle-students"),
    reviewEmpty: document.getElementById("review-empty"),
    reviewWrap: document.getElementById("review-wrap"),
    reviewBody: document.getElementById("review-body"),
    exportXlsx: document.getElementById("export-xlsx"),
  };

  function setNotice(text, visible = true) {
    elements.notice.textContent = text;
    elements.notice.classList.toggle("hidden", !visible);
  }

  function setLoggedIn(loggedIn) {
    elements.loginForm.classList.toggle("hidden", loggedIn);
    elements.classForm.classList.toggle("hidden", !loggedIn);
    if (!loggedIn) {
      state.board = null;
      state.showAllStudents = false;
      elements.classCode.value = "";
      renderDashboard();
      setNotice("输入老师口令");
    }
  }

  function percent(rate) {
    return rate == null ? "—" : `${(rate * 100).toFixed(2)}%`;
  }

  function cell(text, label, numeric = false) {
    const td = document.createElement("td");
    td.textContent = text == null || text === "" ? "—" : String(text);
    td.dataset.label = label;
    if (numeric) td.className = "num";
    return td;
  }

  function renderStudents() {
    const students = state.board ? state.board.students : [];
    elements.studentsBody.replaceChildren();
    if (!students.length) {
      elements.studentsWrap.classList.add("hidden");
      elements.studentsEmpty.classList.remove("hidden");
      elements.studentsEmpty.textContent = "还没导名单";
      elements.toggleStudents.classList.add("hidden");
      return;
    }

    const visible = state.showAllStudents ? students : students.slice(0, 5);
    for (const student of visible) {
      const tr = document.createElement("tr");
      tr.append(
        cell(student.student_no, "学号"),
        cell(student.real_name, "姓名"),
        cell(student.submitted ? "已交" : "未交", "是否已交"),
        cell(student.attempt_count, "作答数", true),
        cell(percent(student.correct_rate), "正确率", true),
      );
      elements.studentsBody.append(tr);
    }
    elements.studentsWrap.classList.remove("hidden");
    elements.studentsEmpty.classList.add("hidden");
    elements.toggleStudents.classList.toggle("hidden", students.length <= 5);
    elements.toggleStudents.textContent = state.showAllStudents ? "收起" : "展开全部";
  }

  function renderMissing() {
    const missing = state.board ? state.board.missing : [];
    elements.missingList.replaceChildren();
    if (!missing.length) {
      elements.missingList.classList.add("hidden");
      elements.missingEmpty.classList.remove("hidden");
      elements.missingEmpty.textContent = state.board && state.board.totals.roster
        ? "全部已交"
        : "还没导名单";
      elements.copyMissing.disabled = true;
      return;
    }
    for (const student of missing) {
      const item = document.createElement("li");
      item.textContent = `${student.student_no} ${student.real_name}`;
      elements.missingList.append(item);
    }
    elements.missingList.classList.remove("hidden");
    elements.missingEmpty.classList.add("hidden");
    elements.copyMissing.disabled = false;
  }

  function renderReview() {
    const review = state.board ? state.board.review_list : [];
    elements.reviewBody.replaceChildren();
    if (!review.length) {
      elements.reviewWrap.classList.add("hidden");
      elements.reviewEmpty.classList.remove("hidden");
      elements.reviewEmpty.textContent = state.board && state.board.totals.attempts
        ? "暂无至少两人做过的题目"
        : "还没有作答数据";
      elements.exportXlsx.disabled = !state.board;
      return;
    }
    for (const item of review) {
      const types = Object.entries(item.error_types || {})
        .sort((a, b) => b[1] - a[1])
        .map(([name, count]) => `${ERROR_LABEL[name] || name} ${count}`)
        .join("，");
      const tr = document.createElement("tr");
      tr.append(
        cell(item.rank, "排名", true),
        cell(item.qid, "题号"),
        cell(item.wrong_devices, "错人数", true),
        cell(item.devices, "做题人数", true),
        cell(percent(item.wrong_rate), "错误率", true),
        cell(item.avg_use_sec == null ? "—" : `${item.avg_use_sec}s`, "平均用时", true),
        cell(types || "—", "错因分布"),
      );
      elements.reviewBody.append(tr);
    }
    elements.reviewWrap.classList.remove("hidden");
    elements.reviewEmpty.classList.add("hidden");
    elements.exportXlsx.disabled = false;
  }

  function renderDashboard() {
    const totals = state.board ? state.board.totals : {
      submitted: 0,
      roster: 0,
      missing: 0,
      attempts: 0,
    };
    elements.submitted.textContent = String(totals.submitted);
    elements.roster.textContent = String(totals.roster);
    elements.missingCount.textContent = String(totals.missing);
    elements.attempts.textContent = String(totals.attempts);
    renderMissing();
    renderStudents();
    renderReview();
  }

  async function parseResponse(response) {
    let data = {};
    try {
      data = await response.json();
    } catch (_) {
      data = {};
    }
    if (!response.ok) {
      const error = new Error(data.detail || `请求失败（${response.status}）`);
      error.status = response.status;
      throw error;
    }
    return data;
  }

  function resetForUnauthorized() {
    state.token = "";
    sessionStorage.removeItem("teacherToken");
    setLoggedIn(false);
    setNotice("登录已失效，请重新输入老师口令");
  }

  async function loadBoard() {
    const classCode = elements.classCode.value.trim();
    if (!classCode) {
      state.board = null;
      renderDashboard();
      setNotice("输入班级码");
      return;
    }
    setNotice("正在加载看板…");
    try {
      const response = await fetch(`/api/board?class_code=${encodeURIComponent(classCode)}`, {
        headers: { Authorization: `Bearer ${state.token}` },
      });
      state.board = await parseResponse(response);
      state.showAllStudents = false;
      renderDashboard();
      setNotice("", false);
    } catch (error) {
      if (error.status === 401) {
        resetForUnauthorized();
      } else {
        setNotice(error.message);
      }
    }
  }

  elements.loginForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      const response = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ password: elements.password.value }),
      });
      const data = await parseResponse(response);
      state.token = data.token;
      sessionStorage.setItem("teacherToken", data.token);
      elements.password.value = "";
      setLoggedIn(true);
      setNotice("输入班级码");
      elements.classCode.focus();
    } catch (error) {
      setNotice(error.message);
    }
  });

  elements.classForm.addEventListener("submit", (event) => {
    event.preventDefault();
    loadBoard();
  });

  elements.logout.addEventListener("click", () => {
    resetForUnauthorized();
  });

  elements.toggleStudents.addEventListener("click", () => {
    state.showAllStudents = !state.showAllStudents;
    renderStudents();
  });

  elements.copyMissing.addEventListener("click", async () => {
    const text = (state.board?.missing || [])
      .map((student) => `${student.student_no} ${student.real_name}`)
      .join("\n");
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
      setNotice("未交名单已复制");
      setTimeout(() => setNotice("", false), 1600);
    } catch (_) {
      const textarea = document.createElement("textarea");
      textarea.value = text;
      textarea.setAttribute("readonly", "");
      textarea.style.position = "fixed";
      textarea.style.opacity = "0";
      document.body.append(textarea);
      textarea.select();
      const copied = document.execCommand("copy");
      textarea.remove();
      setNotice(copied ? "未交名单已复制" : "复制失败，请手动选择名单");
      setTimeout(() => setNotice("", false), 1600);
    }
  });

  elements.exportXlsx.addEventListener("click", async () => {
    const classCode = state.board?.class_code;
    if (!classCode) return;
    try {
      const response = await fetch(`/api/export.xlsx?class_code=${encodeURIComponent(classCode)}`, {
        headers: { Authorization: `Bearer ${state.token}` },
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        if (response.status === 401) resetForUnauthorized();
        throw new Error(data.detail || `导出失败（${response.status}）`);
      }
      const blob = await response.blob();
      const disposition = response.headers.get("Content-Disposition") || "";
      const match = disposition.match(/filename="([^"]+)"/);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = match ? match[1] : `${classCode}_board.xlsx`;
      document.body.append(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    } catch (error) {
      if (!error.status) setNotice(error.message);
    }
  });

  setLoggedIn(Boolean(state.token));
  renderDashboard();
})();
