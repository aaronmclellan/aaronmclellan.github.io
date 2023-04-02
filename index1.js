const app = new Vue({
    el: "#gameBoard",
    data: {
      board: ['', '', '', '', '', '', '', '', ''],
      currentPlayer: 'X',
      winningCombinations: [
        [0, 1, 2],
        [3, 4, 5],
        [6, 7, 8],
        [0, 3, 6],
        [1, 4, 7],
        [2, 5, 8],
        [0, 4, 8],
        [2, 4, 6]
      ]
    },
    methods: {
      makeMove(index) {
        if (this.board[index] === '') {
          this.$set(this.board, index, this.currentPlayer);
          this.checkWinner();
          this.currentPlayer = this.currentPlayer === 'X' ? 'O' : 'X';
        }
      },
      checkWinner() {
        for (let i = 0; i < this.winningCombinations.length; i++) {
          const [a, b, c] = this.winningCombinations[i];
          if (this.board[a] && this.board[a] === this.board[b] && this.board[a] === this.board[c]) {
            document.getElementById(`cell-${a}`).style.backgroundColor = "#8bc34a";
            document.getElementById(`cell-${b}`).style.backgroundColor = "#8bc34a";
            document.getElementById(`cell-${c}`).style.backgroundColor = "#8bc34a";
          }
        }
      },
      resetGame() {
        this.board = ['', '', '', '', '', '', '', '', ''];
        for (let i = 0; i < 9; i++) {
          document.getElementById(`cell-${i}`).style.backgroundColor = "#fff";
        }
      }
    }
  });
  