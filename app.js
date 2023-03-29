// Get references to HTML elements
const taskForm = document.querySelector('form');
const taskInput = document.querySelector('#task-input');
const taskList = document.querySelector('#task-list');

// Check if there are tasks stored in localStorage and parse them
let tasks = localStorage.getItem('tasks') ? JSON.parse(localStorage.getItem('tasks')) : [];

// Display stored tasks on page load
displayTasks();

// Add event listener to form submit button
taskForm.addEventListener('submit', (event) => {
  // Prevent default form submission behavior
  event.preventDefault();

  // Get task input value and clear input field
  const taskText = taskInput.value.trim();
  taskInput.value = '';

  // Return early if input is empty
  if (!taskText) {
    return;
  }

  // Create task object with default completion status of false
  const task = {
    text: taskText,
    completed: false
  };

  // Add task object to tasks array and save to localStorage
  tasks.unshift(task);
  saveTasks();

  // Display updated task list
  displayTasks();
});

// Add event listener to task list items
taskList.addEventListener('click', (event) => {
  // Return early if clicked element is not a task list item
  if (!event.target.classList.contains('list-group-item')) {
    return;
  }

  // Get clicked task index
  const index = event.target.getAttribute('data-index');

  // Update task completion status and save to localStorage
  tasks[index].completed = !tasks[index].completed;
  saveTasks();

  // Display updated task list
  displayTasks();
});


// Function to display tasks in the UI
function displayTasks() {
    // Clear task list HTML
    taskList.innerHTML = '';
  
    // Loop through tasks and create list item HTML for each task
    for (let i = 0; i < tasks.length; i++) {
      const task = tasks[i];
  
      // Create list item element
      const listItem = document.createElement('li');
      listItem.classList.add('list-group-item', 'd-flex', 'align-items-center', 'justify-content-between');
      listItem.setAttribute('data-index', i);
  
      // Create completion status checkbox element
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.checked = task.completed;
      checkbox.addEventListener('change', () => toggleTaskCompletion(i));
      listItem.appendChild(checkbox);
  
      // Create task text element
      const taskText = document.createElement('span');
      taskText.classList.add('ms-3', 'text-wrap');
      taskText.textContent = task.text;
      if (task.completed) {
        taskText.classList.add('text-decoration-line-through');
      }
      listItem.appendChild(taskText);
  
      // Create delete button element
      const deleteButton = document.createElement('button');
      deleteButton.classList.add('btn', 'btn-danger', 'btn-sm');
      deleteButton.innerHTML = '<i class="fas fa-trash"></i>';
      deleteButton.addEventListener('click', () => deleteTask(i));
      listItem.appendChild(deleteButton);
  
      // Append list item to task list HTML
      taskList.appendChild(listItem);
    }
  }
  
  
  // Function to delete a task
function deleteTask(index) {
    // Remove task from tasks array
    tasks.splice(index, 1);
  
    // Save tasks to local storage
    saveTasks();
  
    // Update the UI
    displayTasks();
  }
  
  

// Function to save tasks array to localStorage
function saveTasks() {
  localStorage.setItem('tasks', JSON.stringify(tasks));
}
