// SPDX-License-Identifier: MIT
pragma solidity ^0.8.28;
/// Test-only oracle returning a constant quiescent price (no manipulation surface).
contract FixedTWAP {
    uint256 public immutable price;
    constructor(uint256 _price) { price = _price; }
    function consult() external view returns (uint256) { return price; }
}
