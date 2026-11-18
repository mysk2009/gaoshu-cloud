(() => {
  "use strict";

  const ERROR_LABEL = {
    concept: "概念",
    calc: "计算",
    careless: "看错",
    method: "方法",
    none: "未标注",
  };
  const TOKEN_KEY = "gs_cloud_teacher";
  const LEGACY_TOKEN_KEY = "teacherToken";
  const CLASS_INFO_KEY = "teacherClassInfo";
  const REQUEST_TIMEOUT_MS = 75000;
  const WAKE_MESSAGE =
    "正在连接服务器…（免费实例首次唤醒可能需要 30–60 秒，请勿关闭页面）";
  const CONNECTED_MESSAGE = "服务器已连接";
  const HEALTH_FAILED_MESSAGE = "连不上服务器，请点重试";
  const REQUEST_FAILED_MESSAGE =
    "请求超时：服务器可能正在唤醒，点重试";
  const LOGIN_FAILED_STATUS_MESSAGE = "服务器已连接（登录没成功）";
  const OPERATION_FAILED_STATUS_MESSAGE = "服务器已连接（操作没成功）";
  const AUTO_LOGIN_FAILED_MESSAGE =
    "自动登录失败，请确认班级码和管理口令";
  const AUTO_LOGIN_LOADING_MESSAGE = "正在自动登录…";
  const SECURITY_GATEWAY_MESSAGE =
    "提交被安全网关临时拦截，请稍后重试（如果反复出现，请把学号里的特殊符号去掉）";

  const state = {
    token: readStoredToken(),
    classInfo: readStoredClassInfo(),
    board: null,
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
    "students-wrap", "students-body", "review-empty", "review-wrap",
    "review-body", "export-xlsx", "logout",
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
    sessionStorage.removeItem(TOKEN_KEY);
    sessionStorage.removeItem(LEGACY_TOKEN_KEY);
    sessionStorage.removeItem(CLASS_INFO_KEY);
  }

  function saveSession(data) {
    state.token = data.token;
    state.classInfo = {
      class_code: data.class_code,
      class_name: data.class_name,
      teacher_name: data.teacher_name || "",
      class_secret: data.class_secret || "",
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
      "students-wrap", "review-wrap", "missing-chips",
    ]) {
      elements[id].classList.add("hidden");
    }
    elements["missing-empty"].textContent = "先填老师给你的班级码";
    elements["students-empty"].textContent = "先填老师给你的班级码";
    elements["review-empty"].textContent = "先填老师给你的班级码";
    elements["missing-callout"].classList.add("hidden");
    elements["copy-missing"].disabled = true;
    elements["export-xlsx"].disabled = true;
    for (const id of [
      "stat-submitted", "stat-roster", "stat-missing", "stat-attempts",
    ]) {
      elements[id].textContent = "0";
    }
    elements["students-body"].replaceChildren();
    elements["review-body"].replaceChildren();
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
    setNotice("先填老师给你的班级码");
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
      return;
    }
    for (const student of students) {
      const row = document.createElement("tr");
      row.append(
        textCell(student.student_no, "学号"),
        textCell(student.real_name, "姓名"),
        textCell(student.submitted ? "已交" : "还没交", "已交"),
        numberCell(student.attempt_count, "作答"),
        numberCell(percent(student.correct_rate), "正确率"),
        textCell(student.advice, "建议"),
      );
      row.lastElementChild.classList.add("advice");
      elements["students-body"].append(row);
    }
    elements["students-wrap"].classList.remove("hidden");
    elements["students-empty"].classList.add("hidden");
  }

  function errorTypesFor(item) {
    const types = Object.entries(item?.error_types || {})
      .sort((a, b) => b[1] - a[1])
      .map(([name, count]) => `${ERROR_LABEL[name] || "其他"}（${count} 人）`);
    return types.length ? types.join("、") : "未标注";
  }

  function renderReview() {
    const review = state.board?.review_list || [];
    elements["review-body"].replaceChildren();
    if (!review.length) {
      elements["review-wrap"].classList.add("hidden");
      elements["review-empty"].classList.remove("hidden");
      elements["review-empty"].textContent = state.board?.totals.roster
        ? "这个班目前没有明显共性错题"
        : "这个班还没导名单";
      return;
    }
    for (const item of review) {
      const row = document.createElement("tr");
      row.append(
        numberCell(item.rank, "排名"),
        textCell(item.qid, "题号"),
        numberCell(item.wrong_devices, "错人数"),
        numberCell(item.devices, "做题人数"),
        numberCell(percent(item.wrong_rate), "错误率"),
        numberCell(item.avg_use_sec == null ? "—" : `${item.avg_use_sec} 秒`, "平均用时"),
        textCell(errorTypesFor(item), "错因分布"),
        textCell(item.reason, "为什么讲它"),
      );
      elements["review-body"].append(row);
    }
    elements["review-wrap"].classList.remove("hidden");
    elements["review-empty"].classList.add("hidden");
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
      classSecret ? `学生口令 ${classSecret}` : "",
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
    renderReview();
    elements["export-xlsx"].disabled = false;
    elements["dashboard-section"].classList.remove("loading");
    elements["dashboard-section"].classList.remove("hidden");
  }

  async function loadBoard() {
    if (!state.token || !state.classInfo?.class_code) {
      showEntry();
      setNotice("请先输入班级码和管理口令");
      return;
    }
    setNotice("正在加载看板…");
    const connectionId = showConnecting();
    state.board = null;
    elements["dashboard-section"].classList.remove("hidden");
    elements["dashboard-section"].classList.add("loading");
    try {
      const response = await fetchWithTimeout("/api/board", {
        headers: { Authorization: `Bearer ${state.token}` },
      });
      state.board = await parseResponse(response);
      renderBoard();
      setNotice("", "ok", false);
      clearRetry();
      showConnected(connectionId);
    } catch (error) {
      if (error.status === 401) {
        clearSession();
        showEntry();
        setNotice("登录已失效，请重新输入班级码和管理口令");
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
      setNotice("先填老师给你的班级码");
      return null;
    }
    const connectionId = showConnecting();
    try {
      const response = await fetchWithTimeout("/api/teacher/enter", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          class_code: classCode,
          admin_secret: adminSecret,
        }),
      });
      return await parseResponse(response);
    } catch (error) {
      error.requestId = connectionId;
      throw error;
    }
  }

  async function exportBoard() {
    if (!state.board) return;
    showConnecting();
    const classCode = encodeURIComponent(state.board.class_code || "");
    const response = await fetchWithTimeout(`/api/export.xlsx?class_code=${classCode}`, {
      headers: { Authorization: `Bearer ${state.token}` },
    });
    if (!response.ok) {
      const isJson = hasJsonContentType(response);
      const data = await readJsonBody(response);
      const error = new Error(
        !isJson && response.status >= 400
          ? SECURITY_GATEWAY_MESSAGE
          : data.detail || `导出失败（${response.status}）`,
      );
      error.status = response.status;
      throw error;
    }
    const blob = await response.blob();
    const disposition = response.headers.get("Content-Disposition") || "";
    const match = disposition.match(/filename="([^"]+)"/);
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = match ? match[1] : `${state.board.class_code}_board.xlsx`;
    document.body.append(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    clearRetry();
    showConnected(state.connectionId);
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
      `学生口令：${info.class_secret}`,
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
      setNotice("先填老师给你的班级码");
      return;
    }
    const data = await enterClass(classCode, elements["entry-secret"].value);
    saveSession(data);
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
    if (adminSecret.length < 6) {
      setNotice("管理口令至少 6 位");
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
          admin_secret: adminSecret,
        }),
      });
      const created = await parseResponse(response);
      state.pendingCreate = {
        classCode,
        adminSecret,
        created,
      };
      showStudentCard(created);
      elements["create-form"].classList.add("hidden");
    }
    const entered = await enterClass(
      state.pendingCreate.classCode,
      state.pendingCreate.adminSecret,
    );
    saveSession(entered);
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
    copyText(state.classInfo?.class_secret || "", "学生班级口令已复制");
  });

  elements["refresh-board"].addEventListener("click", () => {
    runButtonAction(elements["refresh-board"], loadBoard);
  });

  elements["export-xlsx"].addEventListener("click", () => {
    runButtonAction(elements["export-xlsx"], exportBoard);
  });

  elements["logout"].addEventListener("click", () => {
    clearSession();
    showEntry();
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
      saveSession(data);
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
      setNotice("请补全班级码和管理口令");
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
