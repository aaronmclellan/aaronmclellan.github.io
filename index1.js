const app = new Vue({
    el: '#app',
    data: {
      squares: Array(9).fill(null),
      xIsNext: true,
      winner: null,
    },
    methods: {
      handleSquareClick(index) {
        if (this.squares[index] || this.winner) {
          return;
        }
        this.$set(this.squares, index, this.xIsNext ? 'X' : 'O');
        this.xIsNext = !this.xIsNext;
        this.checkForWinner();
      },
      checkForWinner() {
        const winningLines = [
          [0, 1, 2],
          [3, 4, 5],
          [6, 7, 8],
          [0, 3, 6],
          [1, 4, 7],
          [2, 5, 8],
          [0, 4, 8],
          [2, 4, 6],
        ];
        for (let i = 0; i < winningLines.length; i++) {
          const [a, b, c] = winningLines[i];
          const squareA = this.squares[a];
          const squareB = this.squares[b];
          const squareC = this.squares[c];
          if (squareA && squareA === squareB && squareA === squareC) {
            this.winner = squareA;
            const resultEl = document.querySelector('.result');
            resultEl.textContent = `${this.winner} is the winner!`;
            const squaresEl = document.querySelectorAll('.square');
            squaresEl[a].classList.add('winner');
            squaresEl[b].classList.add('winner');
            squaresEl[c].classList.add('winner');
            return;
          }
        }
        if (!this.squares.includes(null)) {
          const resultEl = document.querySelector('.result');
          resultEl.textContent = `It's a draw!`;
        }
      },
      handleRestart() {
        this.squares = Array(9).fill(null);
        this.xIsNext = true;
        this.winner = null;
        const resultEl = document.querySelector('.result');
        resultEl.textContent = '';
        const squaresEl = document.querySelectorAll('.square');
        squaresEl.forEach(square => square.classList.remove('winner'));
      },
    },
  });
  
  function renderSquare(i) {
    return `
      <div class="square ${app.squares[i] ? app.squares[i].toLowerCase() : ''}"
           onclick="handleSquareClick(${i})">
        <span>${app.squares[i] ? app.squares[i] : ''}</span>
      </div>
    `;
  }
  