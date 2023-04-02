const app = new Vue({
    el: '#app',
    data: {
      board: ['', '', '', '', '', '', '', '', ''],
      player: 1,
      winner: false,
      winningCombinations: [
        [0, 1, 2],
        [3, 4, 5],
        [6, 7, 8],
        [0, 3, 6],
        [1, 4, 7],
        [2, 5, 8],
        [0, 4, 8],
        [2, 4, 6]
      ],
      winningCells: []
    },
    methods: {
      play(index) {
        if (this.board[index] === '' && !this.winner) {
          this.board.splice(index, 1, this.player === 1 ? 'X' : 'O');
          this.checkWinner();
          this.player = this.player === 1 ? 2 : 1;
        }
      },
      reset() {
        this.board = ['', '', '', '', '', '', '', '', ''];
        this.player = 1;
        this.winner = false;
        this.winningCells = [];
      },
      checkWinner() {
        for (let i = 0; i < this.winningCombinations.length; i++) {
          const [a, b, c] = this.winningCombinations[i];
          if (this.board[a] && this.board[a] === this.board[b] && this.board[a] === this.board[c]) {
            this.winner = true;
            this.winningCells = [a, b, c];
            Swal.fire({
              title: `Player ${this.player} wins!`,
              icon: 'success',
              confirmButtonText: 'Play again',
              onClose: () => {
                this.reset();
              }
            });
            break;
          }
        }
        if (!this.board.includes('') && !this.winner) {
          Swal.fire({
            title: 'It\'s a tie!',
            icon: 'info',
            confirmButtonText: 'Play again',
            onClose: () => {
              this.reset();
            }
          });
        }
      }
    }
  });
  