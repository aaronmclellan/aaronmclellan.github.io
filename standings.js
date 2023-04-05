const API_URL6 = "https://ergast.com/api/f1/current/drivers.json";

new Vue({
    el: "#app",
    data: {
        drivers2: [],
        sortKeyDrivers: "givenName",
        sortOrder: 1,
    },
    computed: {
        sortedDrivers2() {
            console.log(this.sortKeyDrivers);
            if (this.sortKeyDrivers) {
                const key = this.sortKeyDrivers;
                const order = this.sortOrder;
                return this.drivers2.slice().sort((a, b) => {
                    let aValue = a[key];
                    let bValue = b[key];

                    if (key === "dateOfBirth") {
                        aValue = new Date(aValue);
                        bValue = new Date(bValue);
                    }

                    if (typeof aValue === "string") {
                        return order * aValue.localeCompare(bValue);
                    }
                    return order * (aValue - bValue);
                });
            }
            return this.drivers2;
        },
    },
    mounted() {
        this.fetchDrivers2();
    },
    methods: {
        fetchDrivers2() {
            axios.get(API_URL6)
                .then(response => {
                    this.drivers2 = response.data.MRData.DriverTable.Drivers;
                })
                .catch(error => {
                    console.error("Error fetching drivers data:", error);
                });
        },
        sortByDriver(key) {
            if (this.sortKeyDrivers === key) {
                this.sortOrder = -this.sortOrder;
            } else {
                this.sortKeyDrivers = key;
                this.sortOrder = 1;
            }
        },
    },
});
