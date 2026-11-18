(() => {
  "use strict";

  const ERROR_LABEL = {
    concept: "概念错",
    calc: "计算错",
    careless: "看错",
    method: "方法错",
    none: "未标注",
  };
  const REQUEST_TIMEOUT_MS = 75000;
  const SECURITY_GATEWAY_MESSAGE =
    "提交被安全网关临时拦截，请稍后重试（如果反复出现，请把学号里的特殊符号去掉）";

  const state = {
    token: sessionStorage.getItem("teacherToken") || "",
    classInfo: readStoredClassInfo(),
    board: null,
    showAllStudents: false,
    retryAction: null,
    manualPending: new Set(),
  };

  const elements = {
    loginForm: document.getElementById("login-form"),
    classForm: document.getElementById("class-form"),
    dashboard: document.getElementById("dashboard"),
    loginCode: document.getElementById("login-code"),
    password: document.getElementById("password"),
    loginSubmit: document.getElementById("login-submit"),
    classCode: document.getElementById("class-code"),
    logout: document.getElementById("logout"),
    notice: document.getElementById("notice"),
    boardRetry: document.getElementById("board-retry"),
    submitted: document.getElementById("submitted"),
    roster: document.getElementById("roster"),
    missingCount: document.getElementById("missing-count"),
    attempts: document.getElementById("attempts"),
    conclusion: document.getElementById("conclusion"),
    headline: document.getElementById("headline"),
    nextAction: document.getElementById("next-action"),
    missingEmpty: document.getElementById("missing-empty"),
    missingCallout: document.getElementById("missing-callout"),
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
    toast: document.getElementById("toast"),
  };

  function readStoredClassInfo() {
    try {
      return JSON.parse(sessionStorage.getItem("teacherClassInfo") || "null");
    } catch (_) {
      return null;
    }
  }

  function setNotice(text, visible = true) {
    elements.notice.textContent = text;
    elements.notice.classList.toggle("hidden", !visible);
  }

  let toastTimer = null;

  function showToast(text, isError = false) {
    if (!elements.toast) return;
    elements.toast.textContent = text;
    elements.toast.classList.toggle("toast-error", isError);
    elements.toast.classList.remove("hidden");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      elements.toast.classList.add("hidden");
    }, 2400);
  }

  function showRetry(action) {
    state.retryAction = action;
    elements.boardRetry.classList.remove("hidden");
  }

  function clearRetry() {
    state.retryAction = null;
    elements.boardRetry.classList.add("hidden");
  }

  async function fetchWithTimeout(resource, options = {}) {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);
    try {
      return await fetch(resource, {
        ...options,
        signal: controller.signal,
      });
    } catch (error) {
      if (error?.name === "AbortError") {
        const timeoutError = new Error("服务器还没醒，请点「重试」");
        timeoutError.isTimeout = true;
        throw timeoutError;
      }
      const networkError = new Error("网络连接失败，请检查网络后重试");
      networkError.cause = error;
      throw networkError;
    } finally {
      clearTimeout(timeoutId);
    }
  }

  function setLoggedIn(loggedIn) {
    elements.loginForm.classList.toggle("hidden", loggedIn);
    elements.classForm.classList.toggle("hidden", !loggedIn);
    if (loggedIn) {
      elements.classCode.value = state.classInfo?.class_code || "";
    } else {
      state.token = "";
      state.classInfo = null;
      state.board = null;
      state.showAllStudents = false;
      sessionStorage.removeItem("teacherToken");
      sessionStorage.removeItem("teacherClassInfo");
      elements.classCode.value = "";
      renderDashboard();
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

  function textValue(value) {
    return typeof value === "string" ? value.trim() : "";
  }

  function refreshSubmissionSummary() {
    if (!state.board) return;
    const students = Array.isArray(state.board.students)
      ? state.board.students
      : [];
    let submittedCount = 0;
    let manualCount = 0;
    const missing = [];
    for (const student of students) {
      const answered = Number(student.attempt_count || 0) > 0;
      const manual = Boolean(student.manual_submitted);
      student.manual_submitted = manual;
      student.submitted = answered || manual;
      student.submitted_by = answered ? "auto" : (manual ? "manual" : null);
      if (student.submitted) submittedCount += 1;
      if (manual) manualCount += 1;
      if (!student.submitted) {
        missing.push({
          student_no: student.student_no,
          real_name: student.real_name,
          manual_submitted: false,
        });
      }
    }

    const rosterCount = Number(state.board.totals?.roster || students.length);
    const manualSuffix = manualCount
      ? `（其中 ${manualCount} 人手工标记）`
      : "";
    state.board.totals = {
      ...(state.board.totals || {}),
      roster: rosterCount,
      submitted: submittedCount,
      manual_submitted: manualCount,
      missing: missing.length,
    };
    state.board.missing = missing;
    state.board.missing_callout = missing
      .map((student) => student.real_name || student.student_no)
      .join("、");
    state.board.headline = (
      `名单 ${rosterCount} 人，${submittedCount} 人已交`
      + `${manualSuffix}，${missing.length} 人未交`
    );
    const reviewCount = Array.isArray(state.board.review_list)
      ? state.board.review_list.length
      : 0;
    state.board.next_action = reviewCount
      ? `建议这节课先讲下面 ${reviewCount} 道题`
        + (missing.length ? `，再让没交的 ${missing.length} 人补上练习` : "")
      : "这节课没有需要集中讲的题，按逐人建议单独跟进";
  }

  function renderConclusion() {
    if (!state.board) {
      elements.conclusion.classList.add("hidden");
      return;
    }

    const rosterCount = Number(state.board.totals?.roster || 0);
    const emptyRosterText = "还没导名单，先把名单导进来";
    const headline = rosterCount === 0
      ? emptyRosterText
      : textValue(state.board.headline) || "看板数据已更新";
    const nextAction = rosterCount === 0
      ? ""
      : textValue(state.board.next_action);

    elements.headline.textContent = headline;
    elements.nextAction.textContent = nextAction;
    elements.nextAction.classList.toggle("hidden", !nextAction);
    elements.conclusion.classList.remove("hidden");
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
      const statusCell = document.createElement("td");
      statusCell.dataset.label = "是否已交";
      const statusControl = document.createElement("label");
      statusControl.className = "submission-control";
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.checked = Boolean(student.submitted);
      checkbox.disabled = student.submitted_by === "auto";
      checkbox.setAttribute(
        "aria-label",
        `${student.real_name || student.student_no}已交`,
      );
      if (checkbox.disabled) {
        checkbox.title = "已有作答记录，不能在这里取消";
      }
      checkbox.addEventListener("change", () => {
        setManualSubmission(student.student_no, checkbox.checked);
      });
      const submittedLabel = document.createElement("span");
      submittedLabel.textContent = "已交";
      statusControl.append(checkbox, submittedLabel);
      if (student.manual_submitted) {
        const manualTag = document.createElement("span");
        manualTag.className = "submission-source manual";
        manualTag.textContent = "手工";
        statusControl.append(manualTag);
      }
      if (student.submitted_by === "auto") {
        const autoTag = document.createElement("span");
        autoTag.className = "submission-source auto";
        autoTag.textContent = "有作答";
        statusControl.append(autoTag);
      }
      statusCell.append(statusControl);
      tr.append(
        cell(student.student_no, "学号"),
        cell(student.real_name, "姓名"),
        statusCell,
        cell(student.attempt_count, "作答数", true),
        cell(percent(student.correct_rate), "正确率", true),
        cell(student.advice, "建议"),
      );
      elements.studentsBody.append(tr);
    }
    elements.studentsWrap.classList.remove("hidden");
    elements.studentsEmpty.classList.add("hidden");
    elements.toggleStudents.classList.toggle("hidden", students.length <= 5);
    elements.toggleStudents.textContent = state.showAllStudents ? "收起" : "展开全部";
  }

  async function setManualSubmission(studentNo, submitted) {
    if (!state.board || state.manualPending.has(studentNo)) return;
    const student = state.board.students.find(
      (item) => item.student_no === studentNo,
    );
    if (!student) return;

    const previousBoard = JSON.parse(JSON.stringify(state.board));
    state.manualPending.add(studentNo);
    student.manual_submitted = submitted;
    refreshSubmissionSummary();
    renderDashboard();
    try {
      const response = await fetchWithTimeout("/api/admin/manual_submit", {
        method: "POST",
        headers: {
          Authorization: `Bearer ${state.token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          class_code: state.board.class_code,
          student_no: studentNo,
          submitted,
        }),
      });
      const data = await parseResponse(response);
      const current = state.board.students.find(
        (item) => item.student_no === studentNo,
      );
      if (current) current.manual_submitted = Boolean(data.manual_submitted);
      refreshSubmissionSummary();
      renderDashboard();
      showToast(submitted ? "已标记为已交" : "已取消手工标记");
    } catch (error) {
      state.board = previousBoard;
      renderDashboard();
      if (error.status === 401) {
        resetForUnauthorized();
      } else {
        showToast(error?.message || "标记失败，请稍后重试", true);
      }
    } finally {
      state.manualPending.delete(studentNo);
    }
  }

  function renderMissing() {
    const missing = state.board ? state.board.missing : [];
    const rosterCount = Number(state.board?.totals?.roster || 0);
    elements.missingList.replaceChildren();
    if (!state.board) {
      elements.missingCallout.classList.add("hidden");
    } else if (rosterCount === 0) {
      elements.missingCallout.textContent = "还没导名单";
      elements.missingCallout.classList.remove("hidden");
    } else if (!missing.length) {
      elements.missingCallout.textContent = "全员已交";
      elements.missingCallout.classList.remove("hidden");
    } else {
      elements.missingCallout.textContent =
        textValue(state.board.missing_callout) || "请提醒以上同学补交";
      elements.missingCallout.classList.remove("hidden");
    }
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
        cell(item.reason, "为什么讲它"),
      );
      elements.reviewBody.append(tr);
    }
    elements.reviewWrap.classList.remove("hidden");
    elements.reviewEmpty.classList.add("hidden");
    elements.exportXlsx.disabled = false;
  }

  function renderDashboard() {
    elements.dashboard.classList.toggle("loading", !state.board);
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
    renderConclusion();
    renderMissing();
    renderStudents();
    renderReview();
  }

  function hasJsonContentType(response) {
    const contentType = response.headers.get("content-type") || "";
    return contentType.toLowerCase().includes("application/json");
  }

  async function readJsonBody(response) {
    if (!hasJsonContentType(response)) return {};
    try {
      return await response.json();
    } catch (_) {
      return {};
    }
  }

  async function parseResponse(response) {
    const isJson = hasJsonContentType(response);
    const data = await readJsonBody(response);
    if (!response.ok) {
      const message =
        !isJson && response.status >= 400
          ? SECURITY_GATEWAY_MESSAGE
          : data.detail || `请求失败（${response.status}）`;
      const error = new Error(message);
      error.status = response.status;
      throw error;
    }
    return data;
  }

  function resetForUnauthorized(message = "登录已失效，请重新输入班级码和口令") {
    setLoggedIn(false);
    setNotice(message);
  }

  async function loadBoard() {
    if (!state.token || !state.classInfo?.class_code) {
      resetForUnauthorized("请先输入班级码和口令");
      return;
    }
    clearRetry();
    state.board = null;
    renderDashboard();
    setNotice("正在加载看板…");
    try {
      const response = await fetchWithTimeout("/api/board", {
        headers: { Authorization: `Bearer ${state.token}` },
      });
      state.board = await parseResponse(response);
      state.showAllStudents = false;
      renderDashboard();
      setNotice("", false);
      clearRetry();
    } catch (error) {
      if (error.status === 401) {
        resetForUnauthorized();
      } else {
        setNotice(error?.message || "看板加载失败，请稍后重试");
        showRetry(loadBoard);
      }
    }
  }

  async function submitLogin() {
    const classCode = elements.loginCode.value.trim().toUpperCase();
    if (!classCode) {
      setNotice("先填班级码");
      elements.loginCode.focus();
      return;
    }
    const originalText = elements.loginSubmit.textContent;
    clearRetry();
    elements.loginSubmit.disabled = true;
    elements.loginSubmit.textContent = "正在连接…";
    try {
      const enteredSecret = elements.password.value;
      const response = await fetchWithTimeout("/api/teacher/enter", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          class_code: classCode,
          secret: enteredSecret,
        }),
      });
      const data = await parseResponse(response);
      state.token = data.token;
      state.classInfo = {
        class_code: data.class_code,
        class_name: data.class_name,
        teacher_name: data.teacher_name || "",
        class_secret: enteredSecret,
      };
      sessionStorage.setItem("teacherToken", data.token);
      sessionStorage.setItem(
        "teacherClassInfo",
        JSON.stringify(state.classInfo),
      );
      elements.password.value = "";
      setLoggedIn(true);
      await loadBoard();
    } catch (error) {
      setNotice(error?.message || "登录失败，请稍后重试");
      if (error?.isTimeout) {
        showRetry(submitLogin);
      }
    } finally {
      elements.loginSubmit.disabled = false;
      elements.loginSubmit.textContent = originalText;
    }
  }

  elements.loginForm.addEventListener("submit", (event) => {
    event.preventDefault();
    submitLogin();
  });

  elements.classForm.addEventListener("submit", (event) => {
    event.preventDefault();
    loadBoard();
  });

  elements.logout.addEventListener("click", () => {
    resetForUnauthorized("已退出，请重新输入班级码和口令");
  });

  elements.boardRetry.addEventListener("click", () => {
    const action = state.retryAction;
    clearRetry();
    if (action) action();
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
      const response = await fetchWithTimeout(`/api/export.xlsx?class_code=${encodeURIComponent(classCode)}`, {
        headers: { Authorization: `Bearer ${state.token}` },
      });
      if (!response.ok) {
        const isJson = hasJsonContentType(response);
        const data = await readJsonBody(response);
        if (response.status === 401) resetForUnauthorized();
        throw new Error(
          !isJson && response.status >= 400
            ? SECURITY_GATEWAY_MESSAGE
            : data.detail || `导出失败（${response.status}）`,
        );
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
      if (error.status === 401) {
        resetForUnauthorized();
      } else if (error.status) {
        setNotice(error.message);
      } else {
        setNotice(error?.message || "导出失败，请稍后重试");
        showRetry(loadBoard);
      }
    }
  });

  if (state.token && state.classInfo) {
    setLoggedIn(true);
    setNotice("正在加载看板…");
    loadBoard();
  } else {
    setLoggedIn(false);
    setNotice("输入班级码和口令");
  }
  renderDashboard();
})();
