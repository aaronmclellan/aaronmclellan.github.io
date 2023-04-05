const API_URL = "https://ergast.com/api/f1/current/constructorStandings.json";

new Vue({
    el: "#app",
    data: {
        standings: [],
        sortKey: "",
        sortOrder: 1,
    },
    computed: {
        sortedStandings() {
            if (this.sortKey) {
                const key = this.sortKey;
                const order = this.sortOrder;
                return this.standings.slice().sort((a, b) => {
                    let aValue = key === "Constructor" ? a.Constructor.name : a[key];
                    let bValue = key === "Constructor" ? b.Constructor.name : b[key];

                    if (key === "position" || key === "points" || key === "wins") {
                        aValue = parseInt(aValue);
                        bValue = parseInt(bValue);
                    }

                    if (typeof aValue === "string") {
                        return order * aValue.localeCompare(bValue);
                    }
                    return order * (aValue - bValue);
                });
            }
            return this.standings;
        },
    },
    mounted() {
        this.fetchConstructorStandings();
    },
    methods: {
        fetchConstructorStandings() {
            axios.get(API_URL)
                .then(response => {
                    this.standings = response.data.MRData.StandingsTable.StandingsLists[0].ConstructorStandings;
                })
                .catch(error => {
                    console.error("Error fetching constructor standings data:", error);
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
