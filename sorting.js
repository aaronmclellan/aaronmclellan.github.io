// define the Vue.js app
var app = new Vue({
    el: '#app',
    data: {
      unsorted: [], // unsorted lines
      sorted: [] // sorted lines
    },
    methods: {
      // reset the lines to random heights
      reset: function() {
        // generate 20 random heights between 50 and 300
        var heights = [];
        for (var i = 0; i < 20; i++) {
          heights.push(Math.floor(Math.random() * 251) + 50);
        }
        this.unsorted = heights;
        this.sorted = this.bubbleSort(heights.slice());
      },
      // bubble sort algorithm
      bubbleSort: function(array) {
        var swapped;
        do {
          swapped = false;
          for (var i = 0; i < array.length - 1; i++) {
            if (array[i] > array[i + 1]) {
              var temp = array[i];
              array[i] = array[i + 1];
              array[i + 1] = temp;
              swapped = true;
              // update the Vue.js data to trigger reactivity
              this.unsorted = array.slice();
              this.sorted = this.sorted.concat(temp);
            }
          }
        } while (swapped);
        return array;
      }
    },
    mounted: function() {
      // reset the lines to random heights on page load
      this.reset();
    }
  });
  