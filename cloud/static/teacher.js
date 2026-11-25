(() => {
  "use strict";

  const TOKEN_KEY = "gs_cloud_teacher";
  const LEGACY_TOKEN_KEY = "teacherToken";
  const CLASS_INFO_KEY = "teacherClassInfo";
  const LOCAL_SYSTEM_URL = "http://127.0.0.1:8000";
  const REQUEST_TIMEOUT_MS = 75000;
  const WAKE_MESSAGE =
    "正在连接服务器…（启动可能需要等一会儿，请勿关闭页面）";
  const CONNECTED_MESSAGE = "服务器已连接";
  const HEALTH_FAILED_MESSAGE = "连不上服务器，请点重试";
  const REQUEST_FAILED_MESSAGE =
    "请求超时：服务器启动较慢，点重试";
  const LOGIN_FAILED_STATUS_MESSAGE = "服务器已连接（登录没成功）";
  const OPERATION_FAILED_STATUS_MESSAGE = "服务器已连接（操作没成功）";
  const AUTO_LOGIN_FAILED_MESSAGE =
    "自动登录失败，请确认班级码和口令";
  const AUTO_LOGIN_LOADING_MESSAGE = "正在自动登录…";
  const SECURITY_GATEWAY_MESSAGE =
    "提交被安全网关临时拦截，请稍后重试（如果反复出现，请把学号里的特殊符号去掉）";

  const state = {
    token: readStoredToken(),
    classInfo: readStoredClassInfo(),
    board: null,
    showAllStudents: false,
    manualPending: new Set(),
    retryAction: null,
    pendingCreate: null,
    readyTimer: null,
    connectionId: 0,
  };

  const elements = {};
  for (const id of [
    "notice", "connection-status", "connection-text", "retry-action",
    "entry-section", "entry-form", "entry-code", "entry-secret", "entry-submit",
    "show-create", "create-section", "create-form", "create-code", "create-submit",
    "create-class-name", "create-teacher-name", "create-secret",
    "hide-create", "student-card", "card-url", "card-code", "card-secret",
    "card-whole", "copy-whole", "dashboard-section", "board-class-name",
    "board-class-meta", "copy-class-secret", "refresh-board", "headline",
    "next-action", "stat-submitted", "stat-roster", "stat-missing", "stat-attempts",
    "copy-missing", "missing-callout", "missing-empty", "missing-chips", "students-empty",
    "students-wrap", "students-body", "toggle-students", "reset-submissions",
    "exit-board", "toast",
  ]) {
    elements[id] = document.getElementById(id);
  }

  function readStoredToken() {
    return sessionStorage.getItem(TOKEN_KEY)
      || sessionStorage.getItem(LEGACY_TOKEN_KEY)
      || "";
  }

  function normalizeClassCode(value) {
    return typeof value === "string" ? value.trim().toUpperCase() : "";
  }

  function readUrlCredentials() {
    const query = new URLSearchParams(window.location.search);
    const hash = new URLSearchParams(window.location.hash.replace(/^#/, ""));
    const firstValue = (...names) => {
      for (const params of [query, hash]) {
        for (const name of names) {
          const value = params.get(name);
          if (value != null && value !== "") return value;
        }
      }
      return "";
    };
    return {
      classCode: normalizeClassCode(firstValue("cc", "class_code")),
      adminSecret: firstValue("pw", "admin_secret"),
    };
  }

  function clearUrlCredentials(classCode) {
    const query = new URLSearchParams(window.location.search);
    const hash = new URLSearchParams(window.location.hash.replace(/^#/, ""));
    const hasUrlSecret =
      query.has("pw")
      || query.has("admin_secret")
      || hash.has("pw")
      || hash.has("admin_secret");
    if (!hasUrlSecret) return;
    window.history.replaceState(
      null,
      "",
      `/teacher?cc=${encodeURIComponent(classCode)}`,
    );
  }

  function readStoredClassInfo() {
    try {
      return JSON.parse(sessionStorage.getItem(CLASS_INFO_KEY) || "null");
    } catch (_) {
      return null;
    }
  }

  function setNotice(text, tone = "error", visible = true) {
    elements.notice.textContent = text;
    elements.notice.classList.toggle("ok", tone === "ok");
    elements.notice.classList.toggle("hidden", !visible);
  }

  let toastTimer = null;

  function showToast(text, isError = false) {
    elements.toast.textContent = text;
    elements.toast.classList.toggle("toast-error", isError);
    elements.toast.classList.remove("hidden");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      elements.toast.classList.add("hidden");
    }, 2400);
  }

  function showConnecting() {
    clearTimeout(state.readyTimer);
    state.connectionId += 1;
    elements["connection-status"].classList.remove("hidden", "ready");
    elements["connection-text"].textContent = WAKE_MESSAGE;
    return state.connectionId;
  }

  function showConnected(
    connectionId,
    text = CONNECTED_MESSAGE,
    autoHide = true,
  ) {
    if (connectionId !== state.connectionId) return false;
    clearTimeout(state.readyTimer);
    elements["connection-status"].classList.remove("hidden");
    elements["connection-status"].classList.add("ready");
    elements["connection-text"].textContent = text;
    if (autoHide) {
      state.readyTimer = setTimeout(() => {
        if (connectionId === state.connectionId) {
          elements["connection-status"].classList.add("hidden");
        }
      }, 3000);
    }
    return true;
  }

  function showConnectionFailure(connectionId, text = REQUEST_FAILED_MESSAGE) {
    if (connectionId !== state.connectionId) return false;
    clearTimeout(state.readyTimer);
    elements["connection-status"].classList.remove("hidden", "ready");
    elements["connection-text"].textContent = text;
    return true;
  }

  function isConnectionError(error) {
    return Boolean(error?.isTimeout || error?.isNetworkError || error?.cause);
  }

  function showRetry(action) {
    state.retryAction = action;
    elements["retry-action"].classList.remove("hidden");
  }

  function clearRetry() {
    state.retryAction = null;
    elements["retry-action"].classList.add("hidden");
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
        const timeoutError = new Error(REQUEST_FAILED_MESSAGE);
        timeoutError.isTimeout = true;
        throw timeoutError;
      }
      const networkError = new Error("网络连接失败，请检查网络后重试");
      networkError.isNetworkError = true;
      networkError.cause = error;
      throw networkError;
    } finally {
      clearTimeout(timeoutId);
    }
  }

  async function runButtonAction(button, action) {
    if (button.disabled) return;
    const originalText = button.textContent;
    clearRetry();
    button.disabled = true;
    button.textContent = "正在连接…";
    const connectionId = showConnecting();
    try {
      await action();
    } catch (error) {
      setNotice(error?.message || "连接失败，请稍后重试");
      const failureId = error?.requestId || state.connectionId;
      if (isConnectionError(error)) {
        showConnectionFailure(failureId);
      } else {
        showConnected(
          failureId,
          OPERATION_FAILED_STATUS_MESSAGE,
          false,
        );
      }
      showRetry(() => runButtonAction(button, action));
    } finally {
      button.disabled = false;
      button.textContent = originalText;
    }
  }

  function clearSession() {
    state.token = "";
    state.classInfo = null;
    state.board = null;
    state.showAllStudents = false;
    state.manualPending.clear();
    sessionStorage.removeItem(TOKEN_KEY);
    sessionStorage.removeItem(LEGACY_TOKEN_KEY);
    sessionStorage.removeItem(CLASS_INFO_KEY);
  }

  function saveSession(data, secret) {
    state.token = data.token;
    state.classInfo = {
      class_code: data.class_code,
      class_name: data.class_name,
      teacher_name: data.teacher_name || "",
      class_secret: secret || "",
    };
    sessionStorage.setItem(TOKEN_KEY, state.token);
    // Keep the legacy key readable by the retained board page during rollout.
    sessionStorage.setItem(LEGACY_TOKEN_KEY, state.token);
    sessionStorage.setItem(CLASS_INFO_KEY, JSON.stringify(state.classInfo));
  }

  function percent(rate) {
    return rate == null ? "—" : `${(rate * 100).toFixed(1)}%`;
  }

  function textCell(text, label) {
    const cell = document.createElement("td");
    cell.textContent = text == null || text === "" ? "—" : String(text);
    cell.dataset.label = label;
    return cell;
  }

  function numberCell(value, label) {
    const cell = textCell(value, label);
    cell.className = "num";
    return cell;
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

  async function copyText(text, successMessage) {
    if (!text) return;
    try {
      await navigator.clipboard.writeText(text);
    } catch (_) {
      const textarea = document.createElement("textarea");
      textarea.value = text;
      textarea.setAttribute("readonly", "");
      textarea.style.position = "fixed";
      textarea.style.opacity = "0";
      document.body.append(textarea);
      textarea.select();
      document.execCommand("copy");
      textarea.remove();
    }
    setNotice(successMessage, "ok");
    setTimeout(() => setNotice("", "ok", false), 1600);
  }

  function resetDashboard() {
    state.board = null;
    elements["dashboard-section"].classList.add("hidden");
    elements["dashboard-section"].classList.remove("loading");
    for (const id of [
      "students-wrap", "missing-chips",
    ]) {
      elements[id].classList.add("hidden");
    }
    elements["missing-empty"].textContent = "进班后这里显示谁没交";
    elements["students-empty"].textContent = "进班后这里显示每个人的情况";
    elements["missing-callout"].classList.add("hidden");
    elements["copy-missing"].disabled = true;
    for (const id of [
      "stat-submitted", "stat-roster", "stat-missing", "stat-attempts",
    ]) {
      elements[id].textContent = "0";
    }
    elements["students-body"].replaceChildren();
  }

  function showEntry() {
    elements["entry-section"].classList.remove("hidden");
    elements["dashboard-section"].classList.add("hidden");
    elements["create-section"].classList.add("hidden");
    elements["create-form"].classList.remove("hidden");
    elements["student-card"].classList.add("hidden");
    elements["entry-code"].value = "";
    elements["entry-secret"].value = "";
    resetDashboard();
    setNotice("填班级码和口令进班");
  }

  function prefillEntry(credentials) {
    if (credentials.classCode) {
      elements["entry-code"].value = credentials.classCode;
    }
    if (credentials.adminSecret) {
      elements["entry-secret"].value = credentials.adminSecret;
    }
  }

  function renderMissing() {
    const board = state.board;
    const missing = board?.missing || [];
    elements["missing-chips"].replaceChildren();
    elements["missing-callout"].classList.remove("hidden");
    if (!missing.length) {
      elements["missing-callout"].textContent = board?.totals.roster
        ? "全员已交"
        : "还没导名单";
      elements["missing-chips"].classList.add("hidden");
      elements["missing-empty"].classList.remove("hidden");
      elements["missing-empty"].textContent = board?.totals.roster
        ? "全部已交"
        : "这个班还没导名单";
      elements["copy-missing"].disabled = true;
      return;
    }
    elements["missing-callout"].textContent =
      board?.missing_callout || "请提醒未交同学补交";
    for (const student of missing) {
      const chip = document.createElement("span");
      chip.className = "chip";
      chip.textContent = student.real_name
        ? `${student.real_name}（${student.student_no}）`
        : student.student_no;
      elements["missing-chips"].append(chip);
    }
    elements["missing-chips"].classList.remove("hidden");
    elements["missing-empty"].classList.add("hidden");
    elements["copy-missing"].disabled = false;
  }

  function renderStudents() {
    const students = state.board?.students || [];
    elements["students-body"].replaceChildren();
    if (!students.length) {
      elements["students-wrap"].classList.add("hidden");
      elements["students-empty"].classList.remove("hidden");
      elements["students-empty"].textContent = "这个班还没导名单";
      elements["toggle-students"].classList.add("hidden");
      elements["reset-submissions"]?.classList.add("hidden");
      return;
    }
    const visibleStudents = state.showAllStudents
      ? students
      : students.slice(0, 5);
    for (const student of visibleStudents) {
      const row = document.createElement("tr");
      const statusCell = document.createElement("td");
      statusCell.dataset.label = "已交";
      const statusControl = document.createElement("label");
      statusControl.className = "submission-control";
      const checkbox = document.createElement("input");
      checkbox.type = "checkbox";
      checkbox.checked = Boolean(student.submitted);
      checkbox.dataset.studentNo = student.student_no;
      checkbox.dataset.auto = String(student.submitted_by === "auto");
      checkbox.disabled = (
        student.submitted_by === "auto"
        || state.manualPending.has(student.student_no)
      );
      checkbox.setAttribute(
        "aria-label",
        `${student.real_name || student.student_no}已交`,
      );
      if (student.submitted_by === "auto") {
        checkbox.title = "已有作答记录，不能在这里取消";
      } else if (state.manualPending.has(student.student_no)) {
        checkbox.title = "正在保存";
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
      row.append(
        textCell(student.student_no, "学号"),
        textCell(student.real_name, "姓名"),
        statusCell,
        numberCell(student.attempt_count, "作答"),
        numberCell(percent(student.correct_rate), "正确率"),
        textCell(student.advice, "建议"),
      );
      row.lastElementChild.classList.add("advice");
      elements["students-body"].append(row);
    }
    elements["students-wrap"].classList.remove("hidden");
    elements["students-empty"].classList.add("hidden");
    elements["toggle-students"].classList.toggle(
      "hidden",
      students.length <= 5,
    );
    elements["toggle-students"].textContent = (
      state.showAllStudents ? "收起" : "展开全部"
    );
    // 只有存在「已交」的人才需要重置按钮；全班都未交时不该出现
    const anySubmitted = students.some((item) => !!item.submitted);
    if (anySubmitted) {
      elements["reset-submissions"]?.classList.remove("hidden");
    } else {
      elements["reset-submissions"]?.classList.add("hidden");
    }
  }

  async function setManualSubmission(studentNo, submitted) {
    if (!state.board || state.manualPending.has(studentNo)) return;
    const student = state.board.students.find(
      (item) => item.student_no === studentNo,
    );
    if (!student) return;

    const classCode = state.board.class_code;
    const previousStudent = {
      manual_submitted: student.manual_submitted,
      submitted: student.submitted,
      submitted_by: student.submitted_by,
      advice: student.advice,
    };
    const previousTotals = {
      submitted: Boolean(previousStudent.submitted),
      manual_submitted: Boolean(previousStudent.manual_submitted),
    };

    const updateTotals = (before, after) => {
      const totals = state.board.totals || (state.board.totals = {});
      const submittedDelta = Number(Boolean(after.submitted))
        - Number(Boolean(before.submitted));
      const manualDelta = Number(Boolean(after.manual_submitted))
        - Number(Boolean(before.manual_submitted));
      totals.submitted = Math.max(
        0,
        Number(totals.submitted || 0) + submittedDelta,
      );
      totals.missing = Math.max(
        0,
        Number(totals.missing || 0) - submittedDelta,
      );
      totals.manual_submitted = Math.max(
        0,
        Number(totals.manual_submitted || 0) + manualDelta,
      );
      const manualSuffix = totals.manual_submitted
        ? `（其中 ${totals.manual_submitted} 人手工标记）`
        : "";
      state.board.headline = (
        `名单 ${Number(totals.roster || 0)} 人，`
        + `${totals.submitted} 人已交${manualSuffix}，`
        + `${totals.missing} 人未交`
      );
    };

    const setPendingCheckbox = (pending) => {
      const checkbox = Array.from(
        elements["students-body"].querySelectorAll('input[type="checkbox"]'),
      ).find((item) => item.dataset.studentNo === studentNo);
      if (!checkbox) return;
      checkbox.disabled = pending || checkbox.dataset.auto === "true";
      checkbox.title = pending ? "正在保存" : "";
    };

    state.manualPending.add(studentNo);
    student.manual_submitted = submitted;
    student.submitted = Number(student.attempt_count || 0) > 0 || submitted;
    student.submitted_by = Number(student.attempt_count || 0) > 0
      ? "auto"
      : (submitted ? "manual" : null);
    if (!Number(student.attempt_count || 0)) {
      student.advice = submitted ? "已手工标记" : "还没交";
    }
    updateTotals(previousTotals, student);
    renderBoard();
    try {
      const response = await fetchWithTimeout("/api/admin/manual_submit", {
        method: "POST",
        headers: {
          Authorization: `Bearer ${state.token}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          class_code: classCode,
          student_no: studentNo,
          submitted,
        }),
      });
      await parseResponse(response);
      state.manualPending.delete(studentNo);
      setPendingCheckbox(false);
      showToast(submitted ? "已标记为已交" : "已取消手工标记");
    } catch (error) {
      state.manualPending.delete(studentNo);
      const currentStudent = state.board?.students.find(
        (item) => item.student_no === studentNo,
      );
      if (currentStudent) {
        const currentTotals = {
          submitted: Boolean(currentStudent.submitted),
          manual_submitted: Boolean(currentStudent.manual_submitted),
        };
        Object.assign(currentStudent, previousStudent);
        updateTotals(currentTotals, currentStudent);
      }
      renderBoard();
      if (error.status === 401) {
        clearSession();
        showEntry();
        setNotice("登录已失效，请重新输入班级码和口令");
      } else {
        showToast(
          `标记失败：${error?.message || "请稍后重试"}`,
          true,
        );
      }
    }
  }

  function renderBoard() {
    const board = state.board;
    if (!board) return;
    elements["board-class-name"].textContent =
      `当前班级：${board.class_name || board.class_code} · ${board.class_code}`;
    const teacherName = state.classInfo?.teacher_name || "";
    const classSecret = state.classInfo?.class_secret || "";
    elements["board-class-meta"].textContent = [
      teacherName ? `任课老师 ${teacherName}` : "",
      classSecret ? `口令 ${classSecret}` : "",
    ].filter(Boolean).join(" · ");
    elements["headline"].textContent = board.headline;
    elements["next-action"].textContent = board.next_action;
    elements["copy-class-secret"].disabled = !state.classInfo?.class_secret;
    const totals = board.totals || {};
    elements["stat-submitted"].textContent = String(totals.submitted || 0);
    elements["stat-roster"].textContent = String(totals.roster || 0);
    elements["stat-missing"].textContent = String(totals.missing || 0);
    elements["stat-attempts"].textContent = String(totals.attempts || 0);
    renderMissing();
    renderStudents();
    elements["dashboard-section"].classList.remove("loading");
    elements["dashboard-section"].classList.remove("hidden");
  }

  // 静默刷新：只更新数据并重绘，不进入 loading 态、不整页重载，避免看板闪一下看不清
  async function refreshBoardQuietly() {
    if (!state.token) return;
    try {
      const response = await fetchWithTimeout(`/api/board?t=${Date.now()}`, {
        headers: { Authorization: `Bearer ${state.token}` },
        cache: "no-store",
      });
      state.board = await parseResponse(response);
      renderBoard();
    } catch (_) {
      // 静默失败：保留原画面，不打断老师
    }
  }

  async function loadBoard() {
    if (!state.token || !state.classInfo?.class_code) {
      showEntry();
      setNotice("请先输入班级码和口令");
      return;
    }
    setNotice("正在加载看板…");
    const connectionId = showConnecting();
    state.board = null;
    elements["dashboard-section"].classList.remove("hidden");
    elements["dashboard-section"].classList.add("loading");
    try {
      const response = await fetchWithTimeout(`/api/board?t=${Date.now()}`, {
        headers: { Authorization: `Bearer ${state.token}` },
        cache: "no-store",
      });
      state.board = await parseResponse(response);
      state.showAllStudents = false;
      renderBoard();
      setNotice("", "ok", false);
      clearRetry();
      showConnected(connectionId);
    } catch (error) {
      if (error.status === 401) {
        clearSession();
        showEntry();
        setNotice("登录已失效，请重新输入班级码和口令");
        showConnected(connectionId, LOGIN_FAILED_STATUS_MESSAGE, false);
        return;
      }
      setNotice(error?.message || "看板加载失败，请稍后重试");
      if (isConnectionError(error)) {
        showConnectionFailure(connectionId);
      } else {
        showConnected(
          connectionId,
          OPERATION_FAILED_STATUS_MESSAGE,
          false,
        );
      }
      showRetry(loadBoard);
    }
  }

  async function enterClass(classCode, adminSecret) {
    if (!classCode) {
      setNotice("填班级码和口令进班");
      return null;
    }
    const connectionId = showConnecting();
    try {
      const response = await fetchWithTimeout("/api/teacher/enter", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          class_code: classCode,
          secret: adminSecret,
        }),
      });
      return await parseResponse(response);
    } catch (error) {
      error.requestId = connectionId;
      throw error;
    }
  }

  async function checkHealth(onReady) {
    const connectionId = showConnecting();
    try {
      const response = await fetchWithTimeout("/api/health", {
        cache: "no-store",
      });
      if (!response.ok) {
        throw new Error(`健康检查失败（${response.status}）`);
      }
      clearRetry();
      showConnected(connectionId);
      if (connectionId === state.connectionId && typeof onReady === "function") {
        onReady();
      }
    } catch (error) {
      if (connectionId !== state.connectionId) return;
      showConnectionFailure(connectionId, HEALTH_FAILED_MESSAGE);
      setNotice("连不上服务器，请点重试");
      showRetry(() => checkHealth(onReady));
    }
  }

  function showStudentCard(info) {
    const url = window.location.origin;
    const whole = [
      `云端看板：${url}/teacher`,
      `班级码：${info.class_code}`,
      `口令：${info.class_secret}`,
    ].join("\n");
    elements["card-url"].textContent = url;
    elements["card-code"].textContent = info.class_code;
    elements["card-secret"].textContent = info.class_secret;
    elements["card-whole"].textContent = whole;
    elements["student-card"].classList.remove("hidden");
  }

  elements["show-create"].addEventListener("click", () => {
    elements["create-section"].classList.remove("hidden");
    elements["create-code"].focus();
  });

  elements["hide-create"].addEventListener("click", () => {
    elements["create-section"].classList.add("hidden");
  });

  async function submitEntry() {
    const classCode = normalizeClassCode(elements["entry-code"].value);
    if (!classCode) {
      setNotice("填班级码和口令进班");
      return;
    }
    const data = await enterClass(classCode, elements["entry-secret"].value);
    const enteredSecret = elements["entry-secret"].value;
    saveSession(data, enteredSecret);
    elements["entry-secret"].value = "";
    elements["entry-section"].classList.add("hidden");
    elements["create-section"].classList.add("hidden");
    clearUrlCredentials(data.class_code);
    await loadBoard();
  }

  elements["entry-form"].addEventListener("submit", (event) => {
    event.preventDefault();
    runButtonAction(elements["entry-submit"], submitEntry);
  });

  async function submitCreate() {
    const classCode = normalizeClassCode(elements["create-code"].value);
    const adminSecret = elements["create-secret"].value;
    if (!classCode) {
      setNotice("班级码不能为空");
      return;
    }
    if (adminSecret.length < 4 || adminSecret.length > 32) {
      setNotice("口令要 4–32 位");
      return;
    }
    if (!state.pendingCreate) {
      const response = await fetchWithTimeout("/api/teacher/create", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          class_code: classCode,
          class_name: elements["create-class-name"].value,
          teacher_name: elements["create-teacher-name"].value,
          secret: adminSecret,
        }),
      });
      const created = await parseResponse(response);
      state.pendingCreate = {
        classCode,
        adminSecret,
        created,
      };
      showStudentCard({ ...created, class_secret: adminSecret });
      elements["create-form"].classList.add("hidden");
    }
    const entered = await enterClass(
      state.pendingCreate.classCode,
      state.pendingCreate.adminSecret,
    );
    saveSession(entered, state.pendingCreate.adminSecret);
    state.pendingCreate = null;
    elements["entry-section"].classList.add("hidden");
    elements["create-section"].classList.add("hidden");
    clearUrlCredentials(entered.class_code);
    await loadBoard();
    setNotice("班级已建好，学生卡片可以发给学生了", "ok");
  }

  elements["create-form"].addEventListener("submit", (event) => {
    event.preventDefault();
    runButtonAction(elements["create-submit"], submitCreate);
  });

  elements["retry-action"].addEventListener("click", () => {
    const action = state.retryAction;
    clearRetry();
    if (action) action();
  });

  for (const button of document.querySelectorAll("[data-copy-target]")) {
    button.addEventListener("click", () => {
      const target = document.getElementById(button.dataset.copyTarget);
      copyText(target?.textContent || "", "已复制");
    });
  }

  elements["copy-whole"].addEventListener("click", () => {
    copyText(elements["card-whole"].textContent, "整段内容已复制");
  });

  elements["copy-missing"].addEventListener("click", () => {
    const missing = state.board?.missing || [];
    const missingText = missing
      .map((student) => [
        student.student_no,
        student.real_name,
      ].filter(Boolean).join(" "))
      .join("\n");
    copyText(missingText || state.board?.missing_callout || "", "未交名单已复制");
  });

  elements["copy-class-secret"].addEventListener("click", () => {
    copyText(state.classInfo?.class_secret || "", "口令已复制");
  });

  elements["refresh-board"].addEventListener("click", () => {
    runButtonAction(elements["refresh-board"], loadBoard);
  });

  elements["reset-submissions"].addEventListener("click", async () => {
    const ok = window.confirm(
      "把全班重置为「未交」？\n\n历史作答不删除，只是此前的作答不再计入提交状态和榜单，学生可以重新提交。"
    );
    if (!ok) return;
    const button = elements["reset-submissions"];
    const originalText = button.textContent;
    button.disabled = true;
    button.textContent = "重置中…";
    try {
      const response = await fetchWithTimeout("/api/teacher/reset_submissions", {
        method: "POST",
        headers: { Authorization: `Bearer ${state.token}` },
      });
      await parseResponse(response);
      showToast("已重置，全班回到未交");
      await refreshBoardQuietly();
    } catch (error) {
      setNotice(error?.message || "重置失败，请稍后重试");
    } finally {
      button.disabled = false;
      button.textContent = originalText;
    }
  });

  elements["toggle-students"].addEventListener("click", () => {
    state.showAllStudents = !state.showAllStudents;
    renderStudents();
  });

  elements["exit-board"].addEventListener("click", () => {
    clearSession();
    try {
      window.close();
    } catch (_) {
      // Browsers may block closing tabs they did not open; navigate below.
    }
    window.location.href = LOCAL_SYSTEM_URL;
  });

  async function enterFromUrl(credentials) {
    elements["entry-section"].classList.add("hidden");
    elements["create-section"].classList.add("hidden");
    setNotice(AUTO_LOGIN_LOADING_MESSAGE);
    showConnecting();
    try {
      const data = await enterClass(
        credentials.classCode,
        credentials.adminSecret,
      );
      saveSession(data, credentials.adminSecret);
      elements["entry-secret"].value = "";
      clearUrlCredentials(data.class_code);
      await loadBoard();
    } catch (error) {
      clearSession();
      showEntry();
      prefillEntry(credentials);
      setNotice(AUTO_LOGIN_FAILED_MESSAGE);
      const failureId = error?.requestId || state.connectionId;
      if (isConnectionError(error)) {
        showConnectionFailure(failureId);
      } else {
        showConnected(
          failureId,
          LOGIN_FAILED_STATUS_MESSAGE,
          false,
        );
      }
      showRetry(() => enterFromUrl(credentials));
    }
  }

  function routeAfterHealth(urlCredentials) {
    if (urlCredentials.classCode && urlCredentials.adminSecret) {
      enterFromUrl(urlCredentials);
      return;
    }
    if (state.token && state.classInfo) {
      elements["entry-section"].classList.add("hidden");
      loadBoard();
      return;
    }
    showEntry();
    prefillEntry(urlCredentials);
    if (urlCredentials.classCode || urlCredentials.adminSecret) {
      setNotice("请补全班级码和口令");
    }
  }

  async function initialize() {
    resetDashboard();
    const urlCredentials = readUrlCredentials();
    prefillEntry(urlCredentials);
    await checkHealth(() => routeAfterHealth(urlCredentials));
  }

  initialize();
})();
