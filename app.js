const { createApp, nextTick } = Vue;

createApp({
  data() {
    return {
      user: null,
      mode: "login",
      message: "",
      error: "",
      auth: { name: "", email: "", password: "", contact: "", role: "user", gender: "unspecified" },
      dashboard: {},
      treks: [],
      bookings: [],
      users: [],
      notifications: [],
      filters: { q: "", difficulty: "", location: "", duration: "" },
      userSearch: { q: "", role: "" },
      trekForm: this.emptyTrek(),
      staffForm: { name: "", email: "", password: "", contact: "", gender: "unspecified" },
      profileForm: { name: "", contact: "" },
      chart: null,
      page: "dashboard",
    };
  },
  computed: {
    staffOptions() {
      return this.users.filter((u) => u.role === "staff");
    },
    canManageTreks() {
      return this.user && ["admin", "staff"].includes(this.user.role);
    },
    unreadCount() {
      return this.notifications.filter(n => !n.read_at).length;
    },
  },
  methods: {
    emptyTrek() {
      const today = new Date();
      const start = new Date(today.getTime() + 7 * 86400000).toISOString().slice(0, 10);
      const end = new Date(today.getTime() + 10 * 86400000).toISOString().slice(0, 10);
      return {
        id: null,
        name: "",
        location: "",
        difficulty: "Easy",
        duration_days: 3,
        available_slots: 20,
        assigned_staff_id: "",
        status: "Open",
        start_date: start,
        end_date: end,
        notes: "",
        staff_ids: [],
      };
    },
    async api(path, options = {}) {
      const response = await fetch(path, {
        headers: { "Content-Type": "application/json" },
        credentials: "same-origin",
        ...options,
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Something went wrong");
      return data;
    },
    flash(message) {
      this.message = message;
      this.error = "";
      setTimeout(() => (this.message = ""), 3500);
    },
    fail(error) {
      this.error = error.message || String(error);
      this.message = "";
    },
    async bootstrap() {
      const data = await this.api("/api/me");
      this.user = data.user;
      if (this.user) await this.refreshAll();
    },
    async login() {
      try {
        const data = await this.api("/api/login", { method: "POST", body: JSON.stringify(this.auth) });
        this.user = data.user;
        this.profileForm = { name: this.user.name, contact: this.user.contact || "" };
        await this.refreshAll();
        this.flash("Signed in");
      } catch (error) {
        this.fail(error);
      }
    },
    async register() {
      try {
        const data = await this.api("/api/register", { method: "POST", body: JSON.stringify(this.auth) });
        this.user = data.user;
        this.profileForm = { name: this.user.name, contact: this.user.contact || "" };
        await this.refreshAll();
        this.flash("Account created");
      } catch (error) {
        this.fail(error);
      }
    },
    async logout() {
      await this.api("/api/logout", { method: "POST" });
      this.user = null;
      this.auth.email = '';
      this.auth.password = '';
      this.page = "dashboard";
      this.dashboard = {};
      this.treks = [];
      this.bookings = [];
      this.users = [];
      this.notifications = [];
    },
    async refreshAll() {
      await Promise.all([this.loadDashboard(), this.loadTreks(), this.loadBookings(), this.loadNotifications()]);
      if (this.user.role === "admin") await this.loadUsers();
      this.profileForm = { name: this.user.name, contact: this.user.contact || "" };
      await nextTick();
      this.drawChart();
    },
    async loadDashboard() {
      this.dashboard = await this.api("/api/dashboard");
    },
    async loadTreks() {
      const query = new URLSearchParams(Object.entries(this.filters).filter(([, v]) => v)).toString();
      const data = await this.api(`/api/treks${query ? `?${query}` : ""}`);
      this.treks = data.treks;
    },
    async loadBookings() {
      const data = await this.api("/api/bookings");
      this.bookings = data.bookings;
    },
    async loadUsers() {
      const query = new URLSearchParams(Object.entries(this.userSearch).filter(([, v]) => v)).toString();
      const data = await this.api(`/api/users${query ? `?${query}` : ""}`);
      this.users = data.users;
    },
    async loadNotifications() {
      try {
        const data = await this.api("/api/notifications");
        this.notifications = data.notifications;
      } catch (e) {}
    },
    async markNotificationsRead() {
      await this.api("/api/notifications/read", { method: "POST" });
      this.notifications = this.notifications.map(n => ({ ...n, read_at: "read" }));
    },
    async saveTrek() {
      try {
        const body = JSON.stringify(this.trekForm);
        if (this.trekForm.id) {
          await this.api(`/api/treks/${this.trekForm.id}`, { method: "PUT", body });
          this.flash("Trek updated");
        } else {
          await this.api("/api/treks", { method: "POST", body });
          this.flash("Trek created");
        }
        this.trekForm = this.emptyTrek();
        await this.refreshAll();
      } catch (error) {
        this.fail(error);
      }
    },
    async editTrek(trek) {
      this.trekForm = { ...trek, staff_ids: trek.staff_ids || [] };
      this.page = "manage";
      window.scrollTo({ top: 0, behavior: "smooth" });
    },
    async deleteTrek(id) {
      if (!confirm("Remove this trek?")) return;
      try {
        await this.api(`/api/treks/${id}`, { method: "DELETE" });
        await this.refreshAll();
        this.flash("Trek removed");
      } catch (error) {
        this.fail(error);
      }
    },
    async addStaff() {
      try {
        await this.api("/api/staff", { method: "POST", body: JSON.stringify(this.staffForm) });
        this.staffForm = { name: "", email: "", password: "", contact: "", gender: "unspecified" };
        await this.loadUsers();
        this.flash("Staff added");
      } catch (error) {
        this.fail(error);
      }
    },
    async setUserStatus(id, status) {
      try {
        await this.api(`/api/users/${id}/status`, { method: "PUT", body: JSON.stringify({ status }) });
        await this.loadUsers();
        this.flash("Status updated");
      } catch (error) {
        this.fail(error);
      }
    },
    async book(trek, confirmMaleStaff = false) {
      try {
        const res = await this.api("/api/bookings", {
          method: "POST",
          body: JSON.stringify({ trek_id: trek.id, confirm_male_staff: confirmMaleStaff })
        });
        if (res.warning === "no_female_staff") {
          if (confirm("No female staff is assigned to this trek. Would you like to book anyway?")) {
            await this.book(trek, true);
          }
          return;
        }
        await this.refreshAll();
        this.flash("Booking confirmed");
      } catch (error) {
        this.fail(error);
      }
    },
    async setBookingStatus(booking, status) {
      try {
        await this.api(`/api/bookings/${booking.id}`, { method: "PUT", body: JSON.stringify({ status }) });
        await this.refreshAll();
        this.flash("Booking updated");
      } catch (error) {
        this.fail(error);
      }
    },
    async updateProfile() {
      try {
        const data = await this.api("/api/profile", { method: "PUT", body: JSON.stringify(this.profileForm) });
        this.user = data.user;
        this.flash("Profile updated");
      } catch (error) {
        this.fail(error);
      }
    },
    async exportHistory() {
      try {
        const response = await fetch("/api/exports/bookings/download", {
          method: "GET",
          credentials: "include"
        });
        if (!response.ok) throw new Error("Export failed");
        const blob = await response.blob();
        const url = window.URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = "booking_history.csv";
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        window.URL.revokeObjectURL(url);
        this.flash("CSV downloaded successfully");
      } catch (error) {
        this.fail(error);
      }
    },
    drawChart() {
      if (!this.$refs.popularChart || this.user?.role !== "admin") return;
      if (this.chart) this.chart.destroy();
      const popular = this.dashboard.popular || [];
      this.chart = new Chart(this.$refs.popularChart, {
        type: "bar",
        data: {
          labels: popular.map((item) => item.name),
          datasets: [{ label: "Bookings", data: popular.map((item) => item.bookings), backgroundColor: "#276749" }],
        },
        options: { responsive: true, plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true } } },
      });
    },
  },
  mounted() {
    this.bootstrap().catch(() => {});
    this.auth.email = '';
    this.auth.password = '';
  },
  template: `
    <div>
      <nav class="topbar">
        <div class="container-fluid px-4 py-3 d-flex align-items-center justify-content-between gap-3">
          <div class="d-flex align-items-center gap-3">
            <div>
              <h1 class="h4 mb-0">Trekking Management</h1>
              <div class="small text-muted">Routes, staff, bookings and reports</div>
            </div>
          </div>
          <div v-if="user" class="d-flex align-items-center gap-2">
            <span class="badge text-bg-success text-capitalize">{{ user.role }}</span>
            <span class="small d-none d-md-inline">{{ user.name }}</span>
            <div class="position-relative">
              <button class="btn btn-outline-secondary btn-sm" @click="page='notifications'; markNotificationsRead()">
                🔔
                <span v-if="unreadCount > 0" class="position-absolute top-0 start-100 translate-middle badge rounded-pill bg-danger">
                  {{ unreadCount }}
                </span>
              </button>
            </div>
            <button class="btn btn-outline-secondary btn-sm" @click="logout">Logout</button>
          </div>
        </div>
      </nav>

      <div class="d-flex" style="min-height:calc(100vh - 60px)">

        <!-- SIDEBAR -->
        <nav v-if="user" class="sidebar p-3">
          <ul class="nav flex-column gap-1">
            <li><a class="nav-link" :class="page==='dashboard'?'active':''" @click.prevent="page='dashboard'" href="#">Dashboard</a></li>
            <li><a class="nav-link" :class="page==='treks'?'active':''" @click.prevent="page='treks'" href="#">Browse Treks</a></li>
            <li v-if="canManageTreks"><a class="nav-link" :class="page==='manage'?'active':''" @click.prevent="page='manage'" href="#">Manage Treks</a></li>
            <li v-if="user.role==='admin'"><a class="nav-link" :class="page==='staff'?'active':''" @click.prevent="page='staff'" href="#">Staff & Users</a></li>
            <li v-if="user.role==='admin' || user.role==='staff'"><a class="nav-link" :class="page==='bookings'?'active':''" @click.prevent="page='bookings'" href="#">Bookings</a></li>
            <li v-if="user.role==='user'"><a class="nav-link" :class="page==='mybookings'?'active':''" @click.prevent="page='mybookings'" href="#">My Bookings</a></li>
            <li v-if="user.role==='user'"><a class="nav-link" :class="page==='profile'?'active':''" @click.prevent="page='profile'" href="#">Profile</a></li>
            <li><a class="nav-link" :class="page==='notifications'?'active':''" @click.prevent="page='notifications'; markNotificationsRead()" href="#">
              🔔 Notifications
              <span v-if="unreadCount > 0" class="badge bg-danger ms-1">{{ unreadCount }}</span>
            </a></li>
          </ul>
        </nav>

        <!-- MAIN CONTENT -->
        <div class="flex-grow-1 p-4">

          <div v-if="message" class="alert alert-success">{{ message }}</div>
          <div v-if="error" class="alert alert-danger">{{ error }}</div>

          <!-- LOGIN / REGISTER -->
          <section v-if="!user" class="row justify-content-center">
            <div class="col-lg-5">
              <div class="panel p-4">
                <div class="btn-group w-100 mb-4">
                  <button class="btn" :class="mode === 'login' ? 'btn-success' : 'btn-outline-success'" @click="mode = 'login'">Login</button>
                  <button class="btn" :class="mode === 'register' ? 'btn-success' : 'btn-outline-success'" @click="mode = 'register'">Register</button>
                </div>
                <form @submit.prevent="mode === 'login' ? login() : register()" autocomplete="off">
                  <div v-if="mode === 'register'" class="mb-3">
                    <label class="form-label">Name</label>
                    <input v-model="auth.name" class="form-control" required autocomplete="off">
                  </div>
                  <div class="mb-3">
                    <label class="form-label">Email</label>
                    <input v-model="auth.email" type="email" class="form-control" required autocomplete="off">
                  </div>
                  <div v-if="mode === 'register'" class="mb-3">
                    <label class="form-label">Contact</label>
                    <input v-model="auth.contact" class="form-control" autocomplete="off">
                  </div>
                  <div v-if="mode === 'register'" class="mb-3">
                    <label class="form-label">I am registering as</label>
                    <select v-model="auth.role" class="form-select">
                      <option value="user">Trekker (User)</option>
                      <option value="staff">Staff</option>
                    </select>
                  </div>
                  <div v-if="mode === 'register'" class="mb-3">
                    <label class="form-label">Gender</label>
                    <select v-model="auth.gender" class="form-select">
                      <option value="unspecified">Prefer not to say</option>
                      <option value="female">Female</option>
                      <option value="male">Male</option>
                      <option value="other">Other</option>
                    </select>
                  </div>
                  <div class="mb-3">
                    <label class="form-label">Password</label>
                    <input v-model="auth.password" type="password" minlength="6" class="form-control" required autocomplete="new-password">
                  </div>
                  <button class="btn btn-success w-100">{{ mode === 'login' ? 'Login' : 'Create account' }}</button>
                </form>
              </div>
            </div>
          </section>

          <!-- LOGGED IN -->
          <section v-else>

            <!-- DASHBOARD PAGE -->
            <div v-if="page === 'dashboard'">
              <h2 class="h5 mb-3">Dashboard</h2>

              <!-- Admin stats -->
              <div v-if="user.role === 'admin'" class="row g-3 mb-4">
                <div class="col-md-3" v-for="(value, key) in dashboard.stats" :key="key">
                  <div class="panel stat p-3">
                    <div class="text-muted text-capitalize">{{ key }}</div>
                    <div class="display-6">{{ value }}</div>
                  </div>
                </div>
              </div>

              <!-- Monthly report for admin -->
              <div v-if="user.role === 'admin' && dashboard.monthly" class="panel p-3 mb-4">
                <h2 class="h5 mb-3">📊 Monthly Activity Report — {{ dashboard.monthly.month }}</h2>
                <div class="row g-3 mb-3">
                  <div class="col-md-4">
                    <div class="panel stat p-3">
                      <div class="text-muted">Treks Conducted</div>
                      <div class="display-6">{{ dashboard.monthly.treks_conducted }}</div>
                    </div>
                  </div>
                  <div class="col-md-4">
                    <div class="panel stat p-3">
                      <div class="text-muted">Participants</div>
                      <div class="display-6">{{ dashboard.monthly.participants }}</div>
                    </div>
                  </div>
                </div>
                <h6 class="mb-2">Top Treks This Month</h6>
                <ol class="mb-0">
                  <li v-for="t in dashboard.monthly.popular" :key="t.name" class="mb-1">
                    {{ t.name }} — {{ t.bookings }} booking{{ t.bookings !== 1 ? 's' : '' }}
                  </li>
                  <li v-if="!dashboard.monthly.popular || dashboard.monthly.popular.length === 0" class="text-muted">No data yet</li>
                </ol>
              </div>

              <!-- Popular treks chart for admin -->
              <div v-if="user.role === 'admin'" class="panel p-3 mb-4">
                <h2 class="h5">Popular Treks</h2>
                <canvas ref="popularChart" height="100"></canvas>
              </div>

              <!-- Staff assigned treks -->
            <div v-if="user.role === 'staff'" class="panel p-3">
                <h2 class="h5 mb-3">My Assigned Treks</h2>
                <div v-if="!dashboard.assigned || dashboard.assigned.length === 0" class="text-muted">No treks assigned to you yet.</div>
                <div class="row g-4">
                  <div class="col-md-4" v-for="trek in dashboard.assigned" :key="trek.id">
                    <div class="panel h-100 d-flex flex-column" style="border-radius:12px;overflow:hidden">
                      <img src="/static/img/mad2_treck.jpg" style="width:100%;height:160px;object-fit:cover;">
                      <div class="p-3 flex-grow-1 d-flex flex-column">
                        <div class="d-flex justify-content-between align-items-start mb-1">
                          <h5 class="mb-0">{{ trek.name }}</h5>
                          <span class="badge text-bg-light">{{ trek.registered_users }} booked</span>
                        </div>
                        <div class="small text-muted mb-2">{{ trek.location }} · {{ trek.start_date }} to {{ trek.end_date }}</div>
                        <div class="small mb-3">Difficulty: {{ trek.difficulty }}</div>
                        <div class="mt-auto row g-2">
                          <div class="col-6">
                            <label class="form-label small mb-1">Available Slots</label>
                            <input v-model.number="trek.available_slots" type="number" min="0" class="form-control form-control-sm">
                          </div>
                          <div class="col-6">
                            <label class="form-label small mb-1">Status</label>
                            <select v-model="trek.status" class="form-select form-select-sm">
                              <option>Open</option>
                              <option>Closed</option>
                              <option>Completed</option>
                            </select>
                          </div>
                          <div class="col-12">
                            <button class="btn btn-success btn-sm w-100" @click="trekForm = {...trek}; saveTrek()">Save Changes</button>
                          </div>
                        </div>
                      </div>
                    </div>
                  </div>
                </div>
              </div>

              <!-- User booked trips -->
              <div v-if="user.role === 'user'">
                <div class="panel p-3 mb-4">
                  <h2 class="h5">Welcome, {{ user.name }}!</h2>
                  <p class="text-muted">Here are your current bookings.</p>
                </div>
                <div class="panel p-3">
                  <h2 class="h5 mb-3">My Booked Trips</h2>
                  <div v-if="bookings.length === 0" class="text-muted">You have no bookings yet. <a href="#" @click.prevent="page='treks'">Browse treks</a> to get started.</div>
                  <div class="row g-3" v-else>
                    <div class="col-md-6 col-lg-4" v-for="booking in bookings" :key="booking.id">
                      <div class="border rounded p-3 h-100">
                        <div class="d-flex justify-content-between align-items-start">
                          <strong>{{ booking.trek_name }}</strong>
                          <span class="badge" :class="booking.status === 'Booked' ? 'text-bg-success' : 'text-bg-secondary'">{{ booking.status }}</span>
                        </div>
                        <div class="small text-muted mt-1">{{ booking.location }}</div>
                        <div class="small mt-2">{{ booking.start_date }} → {{ booking.end_date }}</div>
                        <button v-if="booking.status === 'Booked'" class="btn btn-outline-warning btn-sm w-100 mt-3" @click="setBookingStatus(booking, 'Cancelled')">Cancel</button>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <!-- BROWSE TREKS PAGE -->
            <div v-if="page === 'treks'">
              <h2 class="h5 mb-3">Browse Treks</h2>
              <div class="d-flex flex-wrap gap-2 align-items-end mb-3">
                <input v-model="filters.q" class="form-control" style="max-width:220px" placeholder="Search treks...">
                <select v-model="filters.difficulty" class="form-select" style="max-width:160px">
                  <option value="">Difficulty: All</option>
                  <option>Easy</option><option>Moderate</option><option>Hard</option>
                </select>
                <input v-model="filters.location" class="form-control" style="max-width:180px" placeholder="Location: All">
                <input v-model="filters.duration" type="number" min="1" class="form-control" style="max-width:130px" placeholder="Max days">
                <button class="btn btn-success" @click="loadTreks">Apply</button>
              </div>
              <div class="row g-4">
                <div class="col-md-4" v-for="trek in treks" :key="trek.id">
                  <div class="panel h-100 d-flex flex-column" style="border-radius:12px;overflow:hidden">
                    <img src="/static/img/mad2_treck.jpg" style="width:100%;height:180px;object-fit:cover;">
                    <div class="p-3 flex-grow-1 d-flex flex-column">
                      <h5 class="mb-1">{{ trek.name }}</h5>
                      <div class="text-muted small mb-2">{{ trek.location }}</div>
                      <div class="small mb-1">{{ trek.difficulty }}</div>
                      <div class="small mb-1">{{ trek.duration_days }} Days</div>
                      <div class="small mb-1">Slots Left: {{ trek.available_slots - (trek.booked_count || 0) }}</div>
                      <div v-if="user.role === 'admin'" class="small mb-3">
                        <strong>Staff:</strong> {{ trek.staff_names || 'Unassigned' }}
                      </div>
                      <div v-else class="small mb-3"></div>
                      <div class="mt-auto d-flex gap-2">
                        <button v-if="user.role === 'user'" class="btn btn-outline-success w-100" :disabled="trek.available_slots - (trek.booked_count || 0) === 0" @click="book(trek)">
                          {{ trek.available_slots - (trek.booked_count || 0) === 0 ? 'Full' : 'Book Now' }}
                        </button>
                        <button v-if="user.role === 'admin'" class="btn btn-outline-secondary btn-sm" @click="editTrek(trek)">Edit</button>
                        <button v-if="user.role === 'admin'" class="btn btn-outline-danger btn-sm" @click="deleteTrek(trek.id)">Remove</button>
                      </div>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <!-- MANAGE TREKS PAGE (admin + staff) -->
            <div v-if="page === 'manage' && canManageTreks">
              <h2 class="h5 mb-3">Manage Treks</h2>
              <div class="panel p-3">
                <form class="row g-3" @submit.prevent="saveTrek">
                  <div class="col-md-6"><label class="form-label">Trek Name</label><input v-model="trekForm.name" class="form-control" required></div>
                  <div class="col-md-6"><label class="form-label">Location</label><input v-model="trekForm.location" class="form-control" required></div>
                  <div class="col-md-4"><label class="form-label">Difficulty</label><select v-model="trekForm.difficulty" class="form-select"><option>Easy</option><option>Moderate</option><option>Hard</option></select></div>
                  <div class="col-md-4"><label class="form-label">Duration</label><input v-model.number="trekForm.duration_days" type="number" min="1" class="form-control" required></div>
                  <div class="col-md-4"><label class="form-label">Slots</label><input v-model.number="trekForm.available_slots" type="number" min="0" class="form-control" required></div>
                  <div class="col-md-4"><label class="form-label">Status</label><select v-model="trekForm.status" class="form-select"><option>Pending</option><option>Approved</option><option>Open</option><option>Closed</option><option>Completed</option></select></div>
                  <div class="col-md-4"><label class="form-label">Start Date</label><input v-model="trekForm.start_date" type="date" class="form-control" required></div>
                  <div class="col-md-4"><label class="form-label">End Date</label><input v-model="trekForm.end_date" type="date" class="form-control" required></div>
                  <div class="col-md-6">
                    <label class="form-label">Assigned Staff (select multiple)</label>
                    <select v-model="trekForm.staff_ids" class="form-select" multiple style="height:120px">
                      <option v-for="staff in staffOptions" :value="staff.id">{{ staff.name }}</option>
                    </select>
                    <div class="form-text">Hold Ctrl (or Cmd on Mac) to select multiple</div>
                  </div>
                  <div class="col-md-6"><label class="form-label">Notes</label><input v-model="trekForm.notes" class="form-control"></div>
                  <div class="col-12 d-flex gap-2"><button class="btn btn-success">{{ trekForm.id ? 'Update trek' : 'Create trek' }}</button><button type="button" class="btn btn-outline-secondary" @click="trekForm = emptyTrek()">Clear</button></div>
                </form>
              </div>
            </div>

            <!-- STAFF & USERS PAGE (admin only) -->
            <div v-if="page === 'staff' && user.role === 'admin'">
              <h2 class="h5 mb-3">Staff & Users</h2>
              <div class="row g-4">
                <div class="col-lg-5">
                  <div class="panel p-3">
                    <h2 class="h5">Add Staff</h2>
                    <form class="row g-3" @submit.prevent="addStaff">
                      <div class="col-md-6"><input v-model="staffForm.name" class="form-control" placeholder="Name" required></div>
                      <div class="col-md-6"><input v-model="staffForm.email" type="email" class="form-control" placeholder="Email" required></div>
                      <div class="col-md-6"><input v-model="staffForm.password" type="password" minlength="6" class="form-control" placeholder="Password" required></div>
                      <div class="col-md-6"><input v-model="staffForm.contact" class="form-control" placeholder="Contact"></div>
                      <div class="col-12">
                        <label class="form-label">Gender</label>
                        <select v-model="staffForm.gender" class="form-select">
                          <option value="unspecified">Prefer not to say</option>
                          <option value="female">Female</option>
                          <option value="male">Male</option>
                          <option value="other">Other</option>
                        </select>
                      </div>
                      <div class="col-12"><button class="btn btn-success">Add staff</button></div>
                    </form>
                  </div>
                </div>
                <div class="col-lg-7">
                  <div class="panel p-3">
                    <div class="d-flex gap-2 mb-3">
                      <input v-model="userSearch.q" @input="loadUsers" class="form-control" placeholder="Search users or staff">
                      <select v-model="userSearch.role" @change="loadUsers" class="form-select w-auto"><option value="">All</option><option>user</option><option>staff</option><option>admin</option></select>
                    </div>
                    <div class="table-responsive">
                      <table class="table table-sm align-middle">
                        <thead><tr><th>Name</th><th>Role</th><th>Gender</th><th>Status</th><th></th></tr></thead>
                        <tbody>
                          <tr v-for="account in users" :key="account.id">
                            <td><div>{{ account.name }}</div><div class="small text-muted">{{ account.email }}</div></td>
                            <td class="text-capitalize">{{ account.role }}</td>
                            <td class="text-capitalize">{{ account.gender }}</td>
                            <td><span class="status-dot me-1" :class="'status-' + account.status"></span>{{ account.status }}</td>
                            <td><select class="form-select form-select-sm" v-model="account.status" @change="setUserStatus(account.id, account.status)" :disabled="account.id === user.id"><option>active</option><option>inactive</option><option>blacklisted</option></select></td>
                          </tr>
                        </tbody>
                      </table>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <!-- BOOKINGS PAGE (admin + staff) -->
            <div v-if="page === 'bookings' && (user.role === 'admin' || user.role === 'staff')">
              <h2 class="h5 mb-3">Bookings</h2>
              <div class="panel p-3">
                <div class="table-responsive">
                  <table class="table table-sm align-middle">
                    <thead><tr><th>User</th><th>Trek</th><th>Location</th><th>Status</th><th>Action</th></tr></thead>
                    <tbody>
                      <tr v-for="booking in bookings" :key="booking.id">
                        <td>{{ booking.user_name }}<div class="small text-muted">{{ booking.user_email }}</div></td>
                        <td>{{ booking.trek_name }}</td><td>{{ booking.location }}</td><td>{{ booking.status }}</td>
                        <td><select v-model="booking.status" class="form-select form-select-sm" @change="setBookingStatus(booking, booking.status)"><option>Booked</option><option>Cancelled</option><option>Completed</option></select></td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </div>
            </div>

            <!-- MY BOOKINGS PAGE (user only) -->
            <div v-if="page === 'mybookings' && user.role === 'user'">
              <div class="d-flex justify-content-between align-items-center mb-3">
                <h2 class="h5 mb-0">My Bookings</h2>
                <button class="btn btn-outline-success btn-sm" @click="exportHistory">Export CSV</button>
              </div>
              <div class="panel p-3">
                <div class="table-responsive">
                  <table class="table table-sm">
                    <thead><tr><th>Trek</th><th>Dates</th><th>Status</th><th></th></tr></thead>
                    <tbody>
                      <tr v-for="booking in bookings" :key="booking.id">
                        <td>{{ booking.trek_name }}</td>
                        <td>{{ booking.start_date }} to {{ booking.end_date }}</td>
                        <td>{{ booking.status }}</td>
                        <td><button v-if="booking.status === 'Booked'" class="btn btn-outline-warning btn-sm" @click="setBookingStatus(booking, 'Cancelled')">Cancel</button></td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </div>
            </div>

            <!-- PROFILE PAGE (user only) -->
            <div v-if="page === 'profile' && user.role === 'user'">
              <h2 class="h5 mb-3">Profile</h2>
              <div class="panel p-3" style="max-width:480px">
                <form @submit.prevent="updateProfile">
                  <label class="form-label">Name</label><input v-model="profileForm.name" class="form-control mb-2" required>
                  <label class="form-label">Contact</label><input v-model="profileForm.contact" class="form-control mb-3">
                  <button class="btn btn-success">Save profile</button>
                </form>
              </div>
            </div>

            <!-- NOTIFICATIONS PAGE -->
            <div v-if="page === 'notifications'">
              <h2 class="h5 mb-3">🔔 Notifications & Reminders</h2>
              <div class="panel p-3">
                <div v-if="notifications.length === 0" class="text-muted">No notifications yet.</div>
                <div v-for="n in notifications" :key="n.id" class="border-bottom py-3" :class="n.read_at ? 'text-muted' : ''">
                  <div :class="n.read_at ? '' : 'fw-semibold'">{{ n.message }}</div>
                  <div class="small text-muted mt-1">{{ n.created_at }}</div>
                </div>
              </div>
            </div>

          </section>
        </div>
      </div>
    </div>
  `,
}).mount("#app");