const API_URL1 = "https://ergast.com/api/f1/current/driverStandings.json";

new Vue({
    el: "#standings",
    data: {
        drivers: [],
        sortKey: "",
        sortOrder: 1,
    },
    computed: {
        sortedDrivers() {
            if (this.sortKey) {
                const key = this.sortKey;
                const order = this.sortOrder;
                return this.drivers.slice().sort((a, b) => {
                    let aValue = key === "constructor" ? a.Constructors[0].name : a[key] || a.Driver[key];
                    let bValue = key === "constructor" ? b.Constructors[0].name : b[key] || b.Driver[key];

                    if (key === "position" || key === "points") {
                        aValue = parseInt(aValue);
                        bValue = parseInt(bValue);
                    }

                    if (typeof aValue === "string") {
                        return order * aValue.localeCompare(bValue);
                    }
                    return order * (aValue - bValue);
                });
            }
            return this.drivers;
        },
    },
    mounted() {
        this.fetchDriverStandings();
    },
    methods: {
        fetchDriverStandings() {
            axios.get(API_URL1)
                .then(response => {
                    this.drivers = response.data.MRData.StandingsTable.StandingsLists[0].DriverStandings;
                })
                .catch(error => {
                    console.error("Error fetching driver standings data:", error);
                });
        },
        sortBy(key) {
            if (this.sortKey === key) {
                this.sortOrder = -this.sortOrder;
            } else {
                this.sortKey = key;
                this.sortOrder = 1;
            }
        },
    },
});
